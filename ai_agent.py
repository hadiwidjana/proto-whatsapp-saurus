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
    balance_deduction_amount: int
    balance_deduction_reason: str

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
        workflow.add_node("calculate_balance", self.calculate_balance_deduction)
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
                "generate_response": "calculate_balance",
                "escalate": "calculate_balance"
            }
        )

        workflow.add_edge("get_business_context", "calculate_balance")
        workflow.add_edge("calculate_balance", "generate_response")
        workflow.add_edge("generate_response", END)
        workflow.add_edge("escalate_to_human", END)

        # Add conditional edge for escalation after balance calculation
        workflow.add_conditional_edges(
            "calculate_balance",
            self.route_after_balance_calc,
            {
                "generate_response": "generate_response",
                "escalate": "escalate_to_human"
            }
        )

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
1. "ai_response" - If you can provide a helpful response immediately (DEFAULT - be confident!)
2. "get_context" - If you need business information (hours, services, pricing, etc.) to respond properly
3. "escalate" - ONLY if the customer explicitly asks for human help OR very specific complex issues

IMPORTANT: Be confident in AI capabilities! Only escalate when:
- Customer explicitly asks for "human", "agent", "representative", "speak to someone", etc.
- Specific order issues with order numbers/IDs that need account access
- Technical problems requiring system access
- Billing/payment disputes requiring account verification
- Legal complaints or threats

DO NOT escalate for:
- General questions about business, services, pricing, hours
- Product information requests
- How-to questions
- General complaints (try to help first)
- Simple troubleshooting
- Basic customer service inquiries

Default to "ai_response" or "get_context" - be helpful and confident!

Conversation history (if any):
{history_context}

Current message: {message_text}

Respond with your decision and reasoning."""

            response = self.llm.invoke([
                SystemMessage(content=system_prompt.format(
                    history_context=history_context,
                    message_text=message_text
                )),
                HumanMessage(content=f"Analyze this message: {message_text}")
            ])

            # Parse the response - be more aggressive about AI handling
            content = response.content.lower()
            message_lower = message_text.lower()

            # Check for explicit human requests
            human_keywords = [
                "human", "agent", "representative", "speak to someone", "talk to someone",
                "customer service", "customer support", "live chat", "real person"
            ]

            explicit_human_request = any(keyword in message_lower for keyword in human_keywords)

            if explicit_human_request:
                decision = "escalate"
                needs_context = False
                confidence_score = 0.9
            elif "get_context" in content or any(word in message_lower for word in ["hours", "open", "closed", "location", "address", "services", "price", "cost", "about"]):
                decision = "get_context"
                needs_context = True
                confidence_score = 0.8
            else:
                # Default to AI response for most cases
                decision = "ai_response"
                needs_context = False
                confidence_score = 0.8

            state.update({
                "decision": decision,
                "confidence_score": confidence_score,
                "needs_business_context": needs_context,
                "reasoning": response.content
            })

            logger.info(f"Message analysis: {decision} (confidence: {confidence_score}) - Message: '{message_text[:50]}...'")
            return state

        except Exception as e:
            logger.error(f"Error analyzing message: {str(e)}")
            # Default to AI response instead of escalation on error
            state.update({
                "decision": "ai_response",
                "confidence_score": 0.5,
                "needs_business_context": False,
                "reasoning": f"Error during analysis, defaulting to AI response: {str(e)}"
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
            business_context = state.get("business_context") or {}
            conversation_history = state.get("conversation_history", [])

            # Prepare business information
            business_info = ""
            business_name = "our business"

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
            else:
                # Handle case where no business context is available
                business_info = "Business information not currently available."

            # Prepare conversation context
            history_context = ""
            if conversation_history:
                recent_messages = conversation_history[-3:]
                history_context = "Recent conversation:\n" + "\n".join([
                    f"{'Customer' if msg.get('message_direction') == 'incoming' else 'Business'}: {msg.get('message_text', '')}"
                    for msg in recent_messages
                ])

            system_prompt = f"""You are a helpful customer service AI assistant for {business_name}. 

Your role:
- Provide friendly, professional responses
- Be helpful and positive
- For simple greetings, respond warmly and ask how you can help
- If you need specific business information that's not available, politely let them know you can get that information
- Always maintain a helpful and conversational tone

{business_info}

{history_context}

Current customer message: {message_text}

Provide a helpful response. Keep it friendly and conversational."""

            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=message_text)
            ])

            state["response_message"] = response.content
            logger.info("AI response generated successfully")
            return state

        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            # Provide a simple fallback response for basic greetings
            message_lower = state.get("message_text", "").lower()
            if any(greeting in message_lower for greeting in ["hi", "hello", "hey", "good morning", "good afternoon"]):
                state["response_message"] = "Hello! 👋 Thanks for reaching out. How can I help you today?"
            else:
                state["response_message"] = "Thank you for your message! How can I assist you today?"
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

    def route_after_balance_calc(self, state: AgentState) -> str:
        """Route after balance calculation"""
        decision = state.get("decision", "escalate")

        if decision == "escalate":
            return "escalate_to_human"
        else:
            return "generate_response"

    def calculate_balance_deduction(self, state: AgentState) -> AgentState:
        """Calculate balance deduction based on effort required for response"""
        try:
            decision = state.get("decision", "ai_response")
            message_text = state.get("message_text", "")
            needs_context = state.get("needs_business_context", False)
            confidence_score = state.get("confidence_score", 0.5)

            # Base deduction amounts (configurable)
            base_amounts = {
                "escalate": 100,          # Minimal effort - just routing to human
                "ai_response": 200,       # Standard AI response
                "get_context": 300        # Higher effort - requires context retrieval + AI processing
            }

            # Get base amount
            base_amount = base_amounts.get(decision, 200)

            # Adjust based on message complexity (length as a simple heuristic)
            message_length = len(message_text)
            complexity_multiplier = 1.0

            if message_length > 200:
                complexity_multiplier = 1.3  # Long messages need more processing
            elif message_length > 100:
                complexity_multiplier = 1.1  # Medium messages

            # Adjust based on confidence (lower confidence = more effort)
            confidence_multiplier = 1.0
            if confidence_score < 0.6:
                confidence_multiplier = 1.2  # Less confident responses require more effort

            # Calculate final amount
            final_amount = int(base_amount * complexity_multiplier * confidence_multiplier)

            # Ensure amount is within reasonable bounds (100-300 rupiah as specified)
            final_amount = max(100, min(300, final_amount))

            # Set deduction details
            reason_map = {
                "escalate": "Message escalation to human support",
                "ai_response": "AI-generated response",
                "get_context": "AI response with business context retrieval"
            }

            state["balance_deduction_amount"] = final_amount
            state["balance_deduction_reason"] = reason_map.get(decision, "AI response processing")

            logger.info(f"Balance deduction calculated: {final_amount} rupiah for {decision} (complexity: {complexity_multiplier}, confidence: {confidence_multiplier})")

            return state

        except Exception as e:
            logger.error(f"Error calculating balance deduction: {str(e)}")
            # Default deduction on error
            state["balance_deduction_amount"] = 200
            state["balance_deduction_reason"] = "AI response processing (default)"
            return state

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

            # Create initial state with serializable data only
            initial_state = AgentState(
                message_text=message_text,
                customer_phone=customer_phone,
                phone_number_id=phone_number_id,
                user_id=user_id,
                business_context=None,
                conversation_history=[],  # Simplified to avoid serialization issues
                decision=None,
                response_message=None,
                needs_business_context=False,
                confidence_score=0.0,
                reasoning="",
                balance_deduction_amount=0,
                balance_deduction_reason=""
            )

            # Store conversation history separately to avoid serialization issues
            self._conversation_history = conversation_history

            # Process through the workflow
            config = {"configurable": {"thread_id": f"{phone_number_id}_{customer_phone}"}}
            result = await self.app.ainvoke(initial_state, config)

            response_message = result.get("response_message")
            deduction_amount = result.get("balance_deduction_amount", 200)
            deduction_reason = result.get("balance_deduction_reason", "AI response processing")

            # Perform balance deduction before sending response
            balance_result = self.db.deduct_user_balance(
                user_id,
                deduction_amount,
                deduction_reason
            )

            if not balance_result["success"]:
                logger.warning(f"Balance deduction failed: {balance_result['message']}")
                # Send a balance warning message instead of the original response
                if "insufficient balance" in balance_result["message"].lower():
                    insufficient_balance_message = f"""⚠️ Insufficient Balance Alert

Your current balance ({balance_result['new_balance']} rupiah) is insufficient to process this request (requires {deduction_amount} rupiah).

Please top up your account to continue using our AI customer service.

For assistance with topping up, please contact our support team."""

                    # Try to send the balance warning
                    balance_warning_sent = self.whatsapp_service.send_message(
                        phone_number_id, customer_phone, insufficient_balance_message
                    )

                    if balance_warning_sent:
                        self.db.save_outgoing_message(
                            phone_number_id, customer_phone, insufficient_balance_message
                        )
                        logger.info(f"Insufficient balance warning sent to {customer_phone}")
                        return insufficient_balance_message

                return None

            logger.info(f"Balance deducted successfully: {deduction_amount} rupiah. New balance: {balance_result['new_balance']}")

            if response_message:
                # Send the response
                success = self.whatsapp_service.send_message(
                    phone_number_id, customer_phone, response_message
                )

                if success:
                    # Save the outgoing message to database with balance info
                    self.db.save_outgoing_message(
                        phone_number_id, customer_phone, response_message
                    )
                    logger.info(f"Response sent successfully to {customer_phone}. Balance deducted: {deduction_amount}")
                    return response_message
                else:
                    logger.error("Failed to send WhatsApp message")
                    # If message sending fails after balance deduction, we should ideally refund
                    # For now, just log the issue
                    logger.error(f"Message sending failed but balance was already deducted: {deduction_amount}")

            return None

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            return None
