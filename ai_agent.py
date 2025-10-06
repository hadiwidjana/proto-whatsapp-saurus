import os
import logging
from typing import Dict, Any, Optional, List, TypedDict
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

class AgentState(TypedDict):
    message_text: str
    customer_phone: str
    phone_number_id: str
    user_id: str
    business_context: Optional[Dict[str, Any]]
    conversation_history: List[Dict[str, Any]]
    decision: Optional[str]  # "ai_response", "human_required", "escalate"
    response_message: Optional[str]
    needs_business_context: bool
    confidence_score: float
    reasoning: str

class WhatsAppAIAgent:
    def __init__(self, db, whatsapp_service):
        self.db = db
        self.whatsapp_service = whatsapp_service
        self.llm = ChatOpenAI(
            model="gpt-4",
            temperature=0.3,
            api_key=os.getenv('OPENAI_API_KEY')
        )

        # Create the workflow graph
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("analyze_message", self.analyze_message)
        workflow.add_node("get_business_context", self.get_business_context)
        workflow.add_node("generate_response", self.generate_response)
        workflow.add_node("escalate_to_human", self.escalate_to_human)

        # Set entry point
        workflow.set_entry_point("analyze_message")

        # Add conditional edges
        workflow.add_conditional_edges(
            "analyze_message",
            self.route_decision,
            {
                "get_context": "get_business_context",
                "generate_response": "generate_response",
                "escalate": "escalate_to_human"
            }
        )

        workflow.add_edge("get_business_context", "generate_response")
        workflow.add_edge("generate_response", END)
        workflow.add_edge("escalate_to_human", END)

        # Compile the graph
        memory = MemorySaver()
        self.app = workflow.compile(checkpointer=memory)

    def analyze_message(self, state: AgentState) -> AgentState:
        """Analyze the incoming message to determine the best course of action"""
        try:
            message_text = state["message_text"]
            conversation_history = state.get("conversation_history", [])

            # Create context from conversation history
            history_context = ""
            if conversation_history:
                recent_messages = conversation_history[-5:]  # Last 5 messages
                history_context = "\n".join([
                    f"{'Customer' if msg.get('message_direction') == 'incoming' else 'Business'}: {msg.get('message_text', '')}"
                    for msg in recent_messages
                ])

            system_prompt = """You are an AI assistant that analyzes customer messages to determine the best response strategy.

Your task is to analyze the customer's message and decide:
1. "ai_response" - If you can provide a helpful response immediately
2. "get_context" - If you need business information (hours, services, pricing, etc.) to respond properly
3. "escalate" - If the message requires human intervention

Consider these factors:
- Simple greetings, basic questions about hours/location/services can often be handled with business context
- Complex complaints, specific orders, technical issues, or emotional situations need human attention
- Requests for specific product details, pricing, or general business info need business context
- Spam, inappropriate content, or unclear messages should be escalated

Conversation history (if any):
{history_context}

Current message: {message_text}

Respond with:
- decision: one of "ai_response", "get_context", "escalate"
- confidence_score: 0.0-1.0 (how confident you are in this decision)
- reasoning: brief explanation of your decision
- needs_business_context: true/false"""

            response = self.llm.invoke([
                SystemMessage(content=system_prompt.format(
                    history_context=history_context,
                    message_text=message_text
                )),
                HumanMessage(content=f"Analyze this message: {message_text}")
            ])

            # Parse the response
            content = response.content.lower()

            if "ai_response" in content:
                decision = "ai_response"
                needs_context = False
            elif "get_context" in content:
                decision = "get_context"
                needs_context = True
            else:
                decision = "escalate"
                needs_context = False

            # Extract confidence score (simplified)
            confidence_score = 0.8 if "high confidence" in content else 0.6

            state.update({
                "decision": decision,
                "confidence_score": confidence_score,
                "needs_business_context": needs_context,
                "reasoning": response.content
            })

            logger.info(f"Message analysis: {decision} (confidence: {confidence_score})")
            return state

        except Exception as e:
            logger.error(f"Error analyzing message: {str(e)}")
            # Default to escalation on error
            state.update({
                "decision": "escalate",
                "confidence_score": 0.3,
                "needs_business_context": False,
                "reasoning": f"Error during analysis: {str(e)}"
            })
            return state

    def get_business_context(self, state: AgentState) -> AgentState:
        """Retrieve business context information"""
        try:
            user_id = state["user_id"]
            business_details = self.db.get_business_details(user_id)

            if business_details:
                state["business_context"] = business_details
                logger.info("Business context retrieved successfully")
            else:
                logger.warning("No business context found")
                state["business_context"] = {}

            return state

        except Exception as e:
            logger.error(f"Error getting business context: {str(e)}")
            state["business_context"] = {}
            return state

    def generate_response(self, state: AgentState) -> AgentState:
        """Generate an AI response based on the message and business context"""
        try:
            message_text = state["message_text"]
            business_context = state.get("business_context", {})
            conversation_history = state.get("conversation_history", [])

            # Prepare business information
            business_info = ""
            if business_context:
                business_name = business_context.get("business_name", "our business")
                description = business_context.get("description", "")
                phone = business_context.get("phone", "")
                email = business_context.get("email", "")
                website = business_context.get("website", "")

                # Opening hours
                opening_hours = business_context.get("opening_hours", {})
                hours_text = ""
                if opening_hours:
                    for day, times in opening_hours.items():
                        if not times.get("closed", False):
                            hours_text += f"{day.capitalize()}: {times.get('open', '')} - {times.get('close', '')}\n"

                # FAQs
                faqs = business_context.get("faqs", [])
                faq_text = ""
                if faqs and faqs[0].get("question"):
                    faq_text = "\nFrequently Asked Questions:\n"
                    for faq in faqs[:3]:  # Limit to 3 FAQs
                        if faq.get("question") and faq.get("answer"):
                            faq_text += f"Q: {faq['question']}\nA: {faq['answer']}\n\n"

                business_info = f"""
Business Information:
- Name: {business_name}
- Description: {description}
- Phone: {phone}
- Email: {email}
- Website: {website}
- Opening Hours:
{hours_text}
{faq_text}
"""

            # Prepare conversation context
            history_context = ""
            if conversation_history:
                recent_messages = conversation_history[-3:]
                history_context = "Recent conversation:\n" + "\n".join([
                    f"{'Customer' if msg.get('message_direction') == 'incoming' else 'Business'}: {msg.get('message_text', '')}"
                    for msg in recent_messages
                ])

            system_prompt = f"""You are a helpful customer service AI assistant for {business_context.get('business_name', 'this business')}. 

Your role:
- Provide helpful, accurate information about the business
- Be friendly, professional, and concise
- Use the business information provided to answer questions
- If you cannot answer something with the available information, politely suggest contacting human support
- Always maintain a helpful and positive tone

{business_info}

{history_context}

Current customer message: {message_text}

Provide a helpful response. Keep it concise but informative."""

            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=message_text)
            ])

            state["response_message"] = response.content
            logger.info("AI response generated successfully")
            return state

        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            state["response_message"] = "I apologize, but I'm having some technical difficulties. A customer service representative will assist you shortly."
            return state

    def escalate_to_human(self, state: AgentState) -> AgentState:
        """Handle escalation to human support"""
        try:
            escalation_message = """Thank you for reaching out! 👋 

I've received your message and our customer service team will get back to you as soon as possible. We typically respond within a few hours during business hours.

If this is urgent, please don't hesitate to call us directly. Thank you for your patience! 🙏"""

            state["response_message"] = escalation_message
            logger.info("Message escalated to human support")
            return state

        except Exception as e:
            logger.error(f"Error in escalation: {str(e)}")
            state["response_message"] = "Thank you for your message. Our customer service team will respond shortly."
            return state

    def route_decision(self, state: AgentState) -> str:
        """Route based on the analysis decision"""
        decision = state.get("decision", "escalate")

        if decision == "get_context":
            return "get_context"
        elif decision == "ai_response":
            return "generate_response"
        else:
            return "escalate"

    async def process_message(self, message_data: Dict[str, Any]) -> Optional[str]:
        """Main entry point for processing a WhatsApp message"""
        try:
            # Extract message details
            phone_number_id = None
            message_text = None
            customer_phone = None

            for entry in message_data.get('entry', []):
                for change in entry.get('changes', []):
                    if change.get('field') == 'messages':
                        value = change.get('value', {})
                        metadata = value.get('metadata', {})
                        messages = value.get('messages', [])

                        phone_number_id = metadata.get('phone_number_id', '')

                        for message in messages:
                            if message.get('type') == 'text':
                                message_text = message.get('text', {}).get('body', '')
                                customer_phone = message.get('from', '')
                                break

            if not all([phone_number_id, message_text, customer_phone]):
                logger.warning("Missing required message data")
                return None

            # Get user information
            user = self.db.get_user_by_phone_number_id(phone_number_id)
            if not user:
                logger.warning(f"No user found for phone_number_id: {phone_number_id}")
                return None

            user_id = str(user.get('_id'))

            # Get conversation history
            conversation_history_result = self.db.get_chat_history(
                phone_number_id, customer_phone, limit=10
            )
            conversation_history = conversation_history_result.get('messages', [])

            # Create initial state
            initial_state = AgentState(
                message_text=message_text,
                customer_phone=customer_phone,
                phone_number_id=phone_number_id,
                user_id=user_id,
                business_context=None,
                conversation_history=conversation_history,
                decision=None,
                response_message=None,
                needs_business_context=False,
                confidence_score=0.0,
                reasoning=""
            )

            # Process through the workflow
            config = {"configurable": {"thread_id": f"{phone_number_id}_{customer_phone}"}}
            result = await self.app.ainvoke(initial_state, config)

            response_message = result.get("response_message")

            if response_message:
                # Send the response
                success = self.whatsapp_service.send_message(
                    phone_number_id, customer_phone, response_message
                )

                if success:
                    # Save the outgoing message to database
                    self.db.save_outgoing_message(
                        phone_number_id, customer_phone, response_message
                    )
                    logger.info(f"Response sent successfully to {customer_phone}")
                    return response_message
                else:
                    logger.error("Failed to send WhatsApp message")

            return None

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            return None
