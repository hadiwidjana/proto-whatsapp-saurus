import os
import logging
from typing import Dict, Any, Optional, List, TypedDict
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from services import EmailService

logger = logging.getLogger(__name__)

class AgentState(TypedDict):
    message_text: str
    customer_phone: str
    phone_number_id: str
    user_id: str
    business_context: Optional[Dict[str, Any]]
    conversation_history: List[Dict[str, Any]]
    decision: Optional[str]  # "ai_response", "human_required", "escalate", "process_order"
    response_message: Optional[str]
    needs_business_context: bool
    confidence_score: float
    reasoning: str
    balance_deduction_amount: int
    balance_deduction_reason: str
    order_intent: bool
    order_details: Optional[str]

class WhatsAppAIAgent:
    def __init__(self, db, whatsapp_service):
        self.db = db
        self.whatsapp_service = whatsapp_service
        self.email_service = EmailService()
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
        workflow.add_node("process_order", self.process_order)

        # Set entry point
        workflow.set_entry_point("analyze_message")

        # Add conditional edges
        workflow.add_conditional_edges(
            "analyze_message",
            self.route_decision,
            {
                "ai_response": "get_business_context",
                "escalate": "get_business_context",
                "process_order": "get_business_context"
            }
        )

        workflow.add_edge("get_business_context", "calculate_balance")

        # Add conditional edge after balance calculation for order processing
        workflow.add_conditional_edges(
            "calculate_balance",
            self.route_after_balance_calc,
            {
                "generate_response": "generate_response",
                "escalate": "escalate_to_human",
                "process_order": "process_order"
            }
        )

        workflow.add_edge("generate_response", END)
        workflow.add_edge("escalate_to_human", END)
        workflow.add_edge("process_order", END)

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

                logger.info(f"History context for LLM:\n{history_context}")
            else:
                logger.info("No history context for LLM")

            system_prompt = """You are an AI assistant that analyzes customer messages to determine the best response strategy.

Your task is to analyze the customer's message and decide:
1. "ai_response" - If you can provide a helpful response immediately (DEFAULT - be confident!)
2. "process_order" - If the customer is expressing interest in purchasing, ordering, or booking services
3. "escalate" - ONLY if the customer explicitly asks for human help OR very specific complex issues

IMPORTANT: Detect order intent when customers say things like:
- "I want to order", "I'd like to buy", "Can I purchase", "I need", "Book me"
- "How much for", "What's the price", "I'm interested in"
- "Sign me up", "Subscribe", "I want to try", "Let's proceed"
- Express interest in products/services after asking about them

Be confident in AI capabilities! Only escalate when:
- Customer explicitly asks for "human", "agent", "representative", "speak to someone", etc.
- Specific order issues with order numbers/IDs that need account access
- Technical problems requiring system access
- Billing/payment disputes requiring account verification
- Legal complaints or threats

For order intent detection, look for:
- Purchase keywords (buy, order, purchase, book, reserve, subscribe)
- Interest expressions (interested, want, need, would like)
- Pricing inquiries followed by positive responses
- Decision-making language (yes, proceed, let's do it, sign me up)

Default to "ai_response" or "process_order" - be helpful and proactive in sales!

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

            # Parse the response - be more aggressive about sales and AI handling
            content = response.content.lower()
            message_lower = message_text.lower()

            # Check for explicit human requests
            human_keywords = [
                "human", "agent", "representative", "speak to someone", "talk to someone",
                "customer service", "customer support", "live chat", "real person"
            ]

            # Check for order intent keywords
            order_keywords = [
                "buy", "order", "purchase", "book", "reserve", "subscribe",
                "interested", "want", "need", "would like", "i'll take",
                "sign me up", "let's proceed", "proceed", "yes", "okay"
            ]

            explicit_human_request = any(keyword in message_lower for keyword in human_keywords)
            order_intent = any(keyword in message_lower for keyword in order_keywords) or "process_order" in content

            if explicit_human_request:
                decision = "escalate"
                needs_context = False
                confidence_score = 0.9
            elif order_intent:
                decision = "process_order"
                needs_context = True
                confidence_score = 0.85
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
                "reasoning": response.content,
                "order_intent": order_intent
            })

            logger.info(f"Message analysis: {decision} (confidence: {confidence_score}) - Order intent: {order_intent} - Message: '{message_text[:50]}...'")
            return state

        except Exception as e:
            logger.error(f"Error analyzing message: {str(e)}")
            # Default to AI response instead of escalation on error
            state.update({
                "decision": "ai_response",
                "confidence_score": 0.5,
                "needs_business_context": False,
                "reasoning": f"Error during analysis, defaulting to AI response: {str(e)}",
                "order_intent": False
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
                service_type = business_context.get("service_type", "")
                pricing_model = business_context.get("pricing_model", "")
                min_order_value = business_context.get("min_order_value", "")

                # Opening hours
                opening_hours = business_context.get("opening_hours", {})
                hours_text = ""
                if opening_hours:
                    for day, times in opening_hours.items():
                        if not times.get("closed", False):
                            hours_text += f"{day.capitalize()}: {times.get('open', '')} - {times.get('close', '')}\n"

                # Products/Services
                products = business_context.get("products", [])
                products_text = ""
                if products:
                    products_text = "\nProducts & Services:\n"
                    for product in products:
                        name = product.get("name", "")
                        desc = product.get("description", "")
                        price = product.get("price", "")
                        category = product.get("category", "")
                        products_text += f"- {name}: {desc}"
                        if price:
                            products_text += f" (Price: {price})"
                        if category:
                            products_text += f" [Category: {category}]"
                        products_text += "\n"

                # Payment methods
                accepted_payments = business_context.get("accepted_payments", [])
                payments_text = ""
                if accepted_payments:
                    payments_text = f"\nAccepted Payment Methods: {', '.join(accepted_payments)}"

                # How to order
                how_to_order = business_context.get("how_to_order", "")
                order_text = ""
                if how_to_order:
                    order_text = f"\nHow to Order:\n{how_to_order}"

                # FAQs
                faqs = business_context.get("faqs", [])
                faq_text = ""
                if faqs and faqs[0].get("question"):
                    faq_text = "\nFrequently Asked Questions:\n"
                    for faq in faqs:  # Show up to 5 FAQs
                        if faq.get("question") and faq.get("answer"):
                            faq_text += f"Q: {faq['question']}\nA: {faq['answer']}\n\n"

                # Build comprehensive business info
                business_info = f"""
Business Information:
- Name: {business_name}
- Description: {description}
- Service Type: {service_type}
- Phone: {phone}
- Email: {email}
- Website: {website}
- Pricing Model: {pricing_model}
- Minimum Order: {min_order_value}

Opening Hours:
{hours_text}
{products_text}
{payments_text}
{order_text}
{faq_text}
"""
            else:
                business_info = "Business information not currently available."

            # Prepare conversation context
            history_context = ""
            if conversation_history:
                recent_messages = conversation_history[-10:]
                history_context = "Recent conversation:\n" + "\n".join([
                    f"{'Customer' if msg.get('message_direction') == 'incoming' else 'Business'}: {msg.get('message_text', '')}"
                    for msg in recent_messages
                ])

                logger.info(f"History context being sent to LLM for response generation:\n{history_context}")
            else:
                logger.info("No history context being sent to LLM for response generation")

            # Get default language for culturally appropriate responses
            default_language = business_context.get("default_language", "en")
            language_context = ""
            if default_language == "id":
                language_context = "Note: This business primarily serves Indonesian customers. Use friendly, professional Indonesian when appropriate, but English is also acceptable."

            system_prompt = f"""You are a helpful customer service AI assistant for {business_name}. 

Your role:
- Provide friendly, professional responses
- Be helpful and positive
- For simple greetings, respond warmly and ask how you can help
- Use the business information provided to answer customer questions accurately
- If customers ask about products, pricing, payment methods, or how to order, use the specific information provided
- Always maintain a helpful and conversational tone
- Use the conversation history to provide contextual responses that reference previous interactions when relevant
{language_context}

{business_info}

{history_context}

Current customer message: {message_text}

Provide a helpful response using the business information above and referencing the conversation history when relevant. Keep it friendly and conversational."""

            logger.info(f"Full system prompt being sent to LLM (truncated):\n{system_prompt[:500]}...")

            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=message_text)
            ])

            state["response_message"] = response.content
            logger.info(f"AI response generated: {response.content[:100]}...")
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
        """Route based on the analysis decision - always get business context first"""
        decision = state.get("decision", "ai_response")

        # Always route to get business context first since we need it for every response
        if decision == "escalate":
            return "escalate"
        elif decision == "process_order":
            return "process_order"
        else:
            return "ai_response"

    def route_after_balance_calc(self, state: AgentState) -> str:
        """Route after balance calculation"""
        decision = state.get("decision", "ai_response")

        if decision == "escalate":
            return "escalate"
        elif decision == "process_order":
            return "process_order"
        else:
            return "generate_response"

    def calculate_balance_deduction(self, state: AgentState) -> AgentState:
        """Calculate balance deduction based on effort required for response"""
        try:
            decision = state.get("decision", "ai_response")
            message_text = state.get("message_text", "")
            confidence_score = state.get("confidence_score", 0.5)
            business_context = state.get("business_context", {})

            # Updated pricing model - business context is always retrieved
            # Base deduction amounts (in rupiah)
            if decision == "escalate":
                # Even escalation now gets business context + routing effort
                base_amount = 0  # Minimal cost for escalation with context
            else:
                # All AI responses now include business context retrieval + AI processing
                base_amount = 50  # Standard cost for AI response with business context

            # Complexity adjustments based on message length
            message_length = len(message_text)
            complexity_multiplier = 1.0

            if message_length > 300:
                complexity_multiplier = 1.4  # Very long messages
            elif message_length > 200:
                complexity_multiplier = 1.25  # Long messages
            elif message_length > 100:
                complexity_multiplier = 1.1   # Medium messages

            # Confidence adjustment (lower confidence = more LLM processing effort)
            confidence_multiplier = 1.0
            if confidence_score < 0.5:
                confidence_multiplier = 1.3  # Much more effort for low confidence
            elif confidence_score < 0.7:
                confidence_multiplier = 1.15  # Slightly more effort

            # Business context richness bonus (more context = higher value)
            context_multiplier = 1.0
            if business_context:
                context_features = 0
                if business_context.get("business_name"):
                    context_features += 1
                if business_context.get("opening_hours"):
                    context_features += 1
                if business_context.get("faqs") and len(business_context.get("faqs", [])) > 0:
                    context_features += 1
                if business_context.get("description"):
                    context_features += 1

                # More complete business context = slightly higher cost (more valuable response)
                if context_features >= 3:
                    context_multiplier = 1.2
                elif context_features >= 2:
                    context_multiplier = 1.1

            # Calculate final amount
            final_amount = int(base_amount * complexity_multiplier * confidence_multiplier * context_multiplier)

            # Ensure amount is within reasonable bounds (25-200 rupiah)
            final_amount = max(25, min(200, final_amount))

            # Updated reason descriptions
            if decision == "escalate":
                reason = "Message escalation with business context lookup"
            else:
                reason = "AI response with business context retrieval and processing"

            state["balance_deduction_amount"] = final_amount
            state["balance_deduction_reason"] = reason

            logger.info(f"Balance deduction calculated: {final_amount} rupiah for {decision} "
                       f"(complexity: {complexity_multiplier:.2f}, confidence: {confidence_multiplier:.2f}, "
                       f"context: {context_multiplier:.2f})")

            return state

        except Exception as e:
            logger.error(f"Error calculating balance deduction: {str(e)}")
            # Default deduction on error
            state["balance_deduction_amount"] = 50
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

            # Convert conversation history to serializable format
            serializable_history = []
            for msg in conversation_history:
                serializable_msg = {
                    'message_text': msg.get('message_text', ''),
                    'message_direction': msg.get('message_direction', ''),
                    'created_at': str(msg.get('created_at', '')),
                    'message_type': msg.get('message_type', 'text')
                }
                serializable_history.append(serializable_msg)

            # Create initial state with serializable data only
            initial_state = AgentState(
                message_text=message_text,
                customer_phone=customer_phone,
                phone_number_id=phone_number_id,
                user_id=user_id,
                business_context=None,
                conversation_history=serializable_history,  # Now properly including conversation history
                decision=None,
                response_message=None,
                needs_business_context=False,
                confidence_score=0.0,
                reasoning="",
                balance_deduction_amount=0,
                balance_deduction_reason="",
                order_intent=False,
                order_details=None
            )

            # Store conversation history separately to avoid serialization issues
            self._conversation_history = conversation_history

            # Process through the workflow
            config = {"configurable": {"thread_id": f"{phone_number_id}_{customer_phone}"}}
            result = await self.app.ainvoke(initial_state, config)

            response_message = result.get("response_message")
            deduction_amount = result.get("balance_deduction_amount", 0)
            deduction_reason = result.get("balance_deduction_reason", "AI response processing")

            # Perform balance deduction before sending response
            balance_result = self.db.deduct_user_balance(
                user_id,
                deduction_amount,
                deduction_reason
            )

            if not balance_result["success"]:
                logger.warning(f"Balance deduction failed: {balance_result['message']}")

                # Check if it's an insufficient balance issue
                if "insufficient balance" in balance_result["message"].lower():
                    # Disable auto-reply for this user
                    self.db.update_whatsapp_auto_reply_enabled(user_id, False)
                    logger.info(f"Auto-reply disabled for user {user_id} due to insufficient balance")

                    # DO NOT send any message when balance is insufficient
                    # Simply return None to indicate no response should be sent
                    return None

                return None

            logger.info(f"Balance deducted successfully: {deduction_amount} rupiah. New balance: {balance_result['new_balance']}")

            if response_message:
                # Send the response
                success = self.whatsapp_service.send_message(
                    phone_number_id, customer_phone, response_message
                )

                if success:
                    # Save the outgoing message to database with cost info
                    self.db.save_outgoing_message(
                        phone_number_id,
                        customer_phone,
                        response_message,
                        cost_amount=deduction_amount,
                        cost_reason=deduction_reason
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

    def process_order(self, state: AgentState) -> AgentState:
        """Process an order based on the message details"""
        try:
            message_text = state["message_text"]
            customer_phone = state["customer_phone"]
            phone_number_id = state["phone_number_id"]
            business_context = state.get("business_context") or {}
            conversation_history = state.get("conversation_history", [])

            logger.info(f"Processing order with message: {message_text}")

            # Prepare conversation context
            history_context = ""
            if conversation_history:
                recent_messages = conversation_history[-5:]
                history_context = "\n".join([
                    f"{'Customer' if msg.get('message_direction') == 'incoming' else 'Business'}: {msg.get('message_text', '')}"
                    for msg in recent_messages
                ])

            # Use LLM to intelligently extract order details
            business_name = business_context.get("business_name", "Business")
            products = business_context.get("products", [])

            products_info = ""
            if products:
                products_info = "Available products/services:\n"
                for product in products:
                    name = product.get("name", "")
                    desc = product.get("description", "")
                    price = product.get("price", "")
                    products_info += f"- {name}: {desc} ({price})\n"

            system_prompt = f"""You are processing an order/reservation request for {business_name}.

Customer message: {message_text}

{products_info}

Recent conversation:
{history_context}

Extract the following order details from the customer's message:
1. What product/service they want (be specific)
2. Quantity or duration requested
3. Any special requirements or preferences
4. Urgency level (urgent, normal, flexible)

Provide a professional order confirmation response that:
- Confirms what they want to order/book
- Mentions the next steps (business owner will contact them)
- Is warm and professional
- Uses appropriate language (Indonesian/English based on customer's language)

Format your response as:
ORDER_DETAILS: [extracted details]
RESPONSE: [confirmation message to customer]"""

            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Process this order request: {message_text}")
            ])

            # Parse LLM response
            llm_response = response.content
            order_details = "Order request received"
            customer_response = "Thank you for your interest! We'll contact you shortly to confirm the details."

            if "ORDER_DETAILS:" in llm_response and "RESPONSE:" in llm_response:
                parts = llm_response.split("RESPONSE:")
                if len(parts) == 2:
                    order_details = parts[0].replace("ORDER_DETAILS:", "").strip()
                    customer_response = parts[1].strip()
            else:
                order_details = llm_response
                # Generate a fallback response
                if any(indonesian in message_text.lower() for indonesian in ["saya", "mau", "bisa", "ingin"]):
                    customer_response = "Terima kasih atas minat Anda! 🎉\n\nKami telah menerima permintaan order/reservasi Anda. Tim kami akan segera menghubungi Anda untuk konfirmasi detail lebih lanjut.\n\nTerima kasih! 🙏"
                else:
                    customer_response = "Thank you for your interest! 🎉\n\nWe've received your order/reservation request. Our team will contact you shortly to confirm the details.\n\nThank you! 🙏"

            state["response_message"] = customer_response
            state["order_details"] = order_details
            state["order_intent"] = True

            logger.info(f"Order processed - Details: {order_details[:100]}...")

            # Send notifications to business owner
            escalation_settings = business_context.get("escalation_settings", {})
            notification_method = escalation_settings.get("method", "email")

            if escalation_settings.get("enabled", False):
                try:
                    # Send email notification
                    if notification_method in ["email", "both"]:
                        email_sent = self.email_service.send_order_notification(
                            business_context, customer_phone, order_details, message_text
                        )
                        if email_sent:
                            logger.info("Order notification email sent successfully")

                    # Send WhatsApp notification
                    if notification_method in ["whatsapp", "both"]:
                        business_phone = escalation_settings.get("whatsappNumber")
                        if business_phone:
                            whatsapp_sent = self.email_service.send_whatsapp_notification(
                                self.whatsapp_service, phone_number_id, business_phone,
                                customer_phone, order_details
                            )
                            if whatsapp_sent:
                                logger.info("Order notification WhatsApp sent successfully")

                except Exception as e:
                    logger.error(f"Error sending order notifications: {str(e)}")

            return state

        except Exception as e:
            logger.error(f"Error processing order: {str(e)}")
            # Fallback response
            if any(indonesian in state.get("message_text", "").lower() for indonesian in ["saya", "mau", "bisa", "ingin"]):
                state["response_message"] = "Terima kasih atas pesan Anda! Kami telah menerima permintaan Anda dan akan segera menghubungi Anda."
            else:
                state["response_message"] = "Thank you for your message! We have received your request and will contact you shortly."
            state["order_details"] = "Order request received"
            return state
