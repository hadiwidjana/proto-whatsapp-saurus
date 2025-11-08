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
    decision: Optional[str]
    response_message: Optional[str]
    needs_business_context: bool
    confidence_score: float
    reasoning: str
    balance_deduction_amount: int
    balance_deduction_reason: str
    order_intent: bool
    order_details: Optional[str]
    ai_config: Optional[Dict[str, Any]]


class WhatsAppAIAgent:
    def __init__(self, db, whatsapp_service):
        self.db = db
        self.whatsapp_service = whatsapp_service
        self.email_service = EmailService()

        self.default_model = "gpt-4"
        self.default_temperature = 0.3

        self.llm = None

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

        workflow.add_conditional_edges(
            "get_business_context",
            self.route_after_context,
            {
                "generate_response": "generate_response",
                "escalate": "escalate_to_human",
                "process_order": "process_order"
            }
        )

        workflow.add_edge("generate_response", "calculate_balance")
        workflow.add_edge("escalate_to_human", "calculate_balance")
        workflow.add_edge("process_order", "calculate_balance")
        workflow.add_edge("calculate_balance", END)

        # Compile the graph
        memory = MemorySaver()
        self.app = workflow.compile(checkpointer=memory)

    def _get_llm_for_config(self, ai_config: Optional[Dict[str, Any]]) -> ChatOpenAI:
        """Get or create LLM instance based on AI configuration"""
        model = self.default_model
        temperature = self.default_temperature

        if ai_config:
            model = ai_config.get('model', self.default_model)
            creativity = ai_config.get('creativity', 2)
            temperature = self._map_creativity_to_temperature(creativity)

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=os.getenv('OPENAI_API_KEY')
        )

    def _map_creativity_to_temperature(self, creativity: int) -> float:
        """Convert creativity level (0-4) to temperature value
        0 = Very strict (0.0)
        1 = Strict (0.2)
        2 = Balanced (0.5)
        3 = Creative (0.7)
        4 = Very creative (0.9)
        """
        temperature_map = {
            0: 0.0,
            1: 0.2,
            2: 0.5,
            3: 0.7,
            4: 0.9
        }
        return temperature_map.get(creativity, 0.5)

    def _get_formality_context(self, formality: int) -> str:
        """Convert formality level (0-4) to context string
        0 = Very formal
        1 = Formal
        2 = Balanced
        3 = Casual
        4 = Very casual
        """
        formality_map = {
            0: "Use very formal, professional language. Be extremely polite, respectful, and use proper titles. Avoid contractions and colloquialisms.",
            1: "Use formal, professional language. Be polite and respectful with proper grammar.",
            2: "Use balanced, professional yet friendly language. Be approachable while maintaining professionalism.",
            3: "Use casual, friendly language. Be conversational and warm. You can use contractions and informal expressions.",
            4: "Use very casual, friendly language. Be super conversational, warm, and use emojis when appropriate. Feel free to be playful."
        }
        return formality_map.get(formality, "Use balanced, professional yet friendly language.")

    def _get_max_tokens_from_reply_length(self, max_reply_length: int) -> int:
        """Convert maxReplyLength setting (0-4) to token limit
        0 = Very short (500 tokens)
        1 = Short (1000 tokens)
        2 = Medium (2500 tokens)
        3 = Long (5000 tokens)
        4 = Very long (10000 tokens)
        """
        length_map = {
            0: 500,
            1: 1000,
            2: 2500,
            3: 5000,
            4: 10000
        }
        return length_map.get(max_reply_length, 300)

    def analyze_message(self, state: AgentState) -> AgentState:
        """Analyze the incoming message to determine the best course of action"""
        try:
            message_text = state["message_text"]
            conversation_history = state.get("conversation_history", [])
            ai_config = state.get("ai_config")

            llm = self._get_llm_for_config(ai_config)

            history_context = ""
            if conversation_history:
                recent_messages = conversation_history[-5:]
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
- Billing/payment disputes requiring account verification

Default to "ai_response" or "process_order" - be helpful and proactive in sales!

Conversation history (if any):
{history_context}

Current message: {message_text}

Respond with your decision and reasoning."""

            response = llm.invoke([
                SystemMessage(content=system_prompt.format(
                    history_context=history_context,
                    message_text=message_text
                )),
                HumanMessage(content=f"Analyze this message: {message_text}")
            ])

            content = response.content.lower()
            message_lower = message_text.lower()

            human_keywords = [
                "human", "agent", "representative", "speak to someone", "talk to someone",
                "customer service", "customer support", "live chat", "real person"
            ]

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
            elif "get_context" in content or any(word in message_lower for word in
                                                 ["hours", "open", "closed", "location", "address", "services", "price",
                                                  "cost", "about"]):
                decision = "get_context"
                needs_context = True
                confidence_score = 0.8
            else:
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

            logger.info(
                f"Message analysis: {decision} (confidence: {confidence_score}) - Order intent: {order_intent} - Message: '{message_text[:50]}...'")
            return state

        except Exception as e:
            logger.error(f"Error analyzing message: {str(e)}")
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
            ai_config = state.get("ai_config")

            llm = self._get_llm_for_config(ai_config)

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

                opening_hours = business_context.get("opening_hours", {})
                hours_text = ""
                if opening_hours:
                    for day, times in opening_hours.items():
                        if not times.get("closed", False):
                            hours_text += f"{day.capitalize()}: {times.get('open', '')} - {times.get('close', '')}\n"

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

                accepted_payments = business_context.get("accepted_payments", [])
                payments_text = ""
                if accepted_payments:
                    payments_text = f"\nAccepted Payment Methods: {', '.join(accepted_payments)}"

                how_to_order = business_context.get("how_to_order", "")
                order_text = ""
                if how_to_order:
                    order_text = f"\nHow to Order:\n{how_to_order}"

                faqs = business_context.get("faqs", [])
                faq_text = ""
                if faqs and faqs[0].get("question"):
                    faq_text = "\nFrequently Asked Questions:\n"
                    for faq in faqs:
                        if faq.get("question") and faq.get("answer"):
                            faq_text += f"Q: {faq['question']}\nA: {faq['answer']}\n\n"

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

            default_language = business_context.get("default_language", "en")
            language_context = ""
            if default_language == "id":
                language_context = "Note: This business primarily serves Indonesian customers. Use friendly, professional Indonesian when appropriate, but English is also acceptable."

            formality_context = ""
            if ai_config and ai_config.get('formality'):
                formality_context = self._get_formality_context(ai_config.get('formality', 2))

            if ai_config and ai_config.get('systemPrompt'):
                system_prompt = ai_config.get('systemPrompt')
                system_prompt += f"\n\n{business_info}\n\n{history_context}\n\nCurrent customer message: {message_text}\n\nProvide a helpful response using the business information above."
            else:
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
{formality_context}

{business_info}

{history_context}

Current customer message: {message_text}

Provide a helpful response using the business information above and referencing the conversation history when relevant. Keep it friendly and conversational."""

            logger.info(f"Full system prompt being sent to LLM (truncated):\n{system_prompt[:500]}...")

            max_tokens = None
            if ai_config and ai_config.get('maxReplyLength') is not None:
                max_tokens = self._get_max_tokens_from_reply_length(ai_config.get('maxReplyLength', 2))
                logger.info(f"Using max_tokens limit: {max_tokens}")

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=message_text)
            ]

            if max_tokens:
                response = llm.invoke(messages, max_completion_tokens=max_tokens)
            else:
                response = llm.invoke(messages)

            response_text = response.content.strip() if response.content else ""

            if not response_text:
                logger.warning("LLM returned empty response, using fallback")
                message_lower = message_text.lower()
                if any(greeting in message_lower for greeting in ["hi", "hello", "hey", "halo", "hai", "selamat"]):
                    response_text = "Hello! 👋 Thanks for reaching out. How can I help you today?"
                else:
                    response_text = "Thank you for your message! How can I assist you today?"

            state["response_message"] = response_text
            logger.info(f"AI response generated: {response_text[:100]}...")
            return state

        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
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

    def route_after_context(self, state: AgentState) -> str:
        """Route after context retrieval"""
        decision = state.get("decision", "ai_response")

        if decision == "escalate":
            return "escalate"
        elif decision == "process_order":
            return "process_order"
        else:
            return "generate_response"

    def calculate_balance_deduction(self, state: AgentState) -> AgentState:
        """Calculate balance deduction based on AI model and response length"""
        try:
            decision = state.get("decision", "ai_response")
            response_message = state.get("response_message", "")
            ai_config = state.get("ai_config")

            model_pricing = {
                "gpt-5": 74,
                "gpt-5-mini": 35,
                "gpt-5-nano": 27,
                "gpt-5-pro": 612,
                "gpt-4.1": 116,
                "gpt-4.5-preview": 30,
                "gpt-3.5-turbo": 40,
            }

            default_model = self.default_model
            model_name = default_model

            if ai_config and ai_config.get('model'):
                model_name = ai_config.get('model')

            base_cost = model_pricing.get(model_name, 50)

            if decision == "escalate":
                final_amount = 0
                reason = "Message escalation (no AI processing)"
            else:
                word_count = len(response_message.split()) if response_message else 0

                if word_count <= 100:
                    final_amount = base_cost
                else:
                    extra_words = word_count - 100
                    additional_cost = int((extra_words / 100) * base_cost)
                    final_amount = base_cost + additional_cost

                reason = f"AI response using {model_name} ({word_count} words)"

            state["balance_deduction_amount"] = final_amount
            state["balance_deduction_reason"] = reason

            logger.info(f"Balance deduction calculated: {final_amount} IDR for {decision} using {model_name}")

            return state

        except Exception as e:
            logger.error(f"Error calculating balance deduction: {str(e)}")
            state["balance_deduction_amount"] = 50
            state["balance_deduction_reason"] = "AI response processing (default)"
            return state

    async def process_message(self, message_data: Dict[str, Any]) -> Optional[str]:
        """Main entry point for processing a WhatsApp message"""
        try:
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

            # Check if auto-reply is enabled for this user
            if not user.get('whatsapp_auto_reply_enabled', False):
                logger.info(f"Auto-reply is disabled for user {user_id}. Skipping message processing.")
                return None

            # Get AI configuration for this user
            ai_config = self.db.get_ai_config(user_id)
            if ai_config:
                logger.info(
                    f"AI config loaded for user {user_id}: model={ai_config.get('model')}, creativity={ai_config.get('creativity')}, formality={ai_config.get('formality')}")

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
                conversation_history=serializable_history,
                decision=None,
                response_message=None,
                needs_business_context=False,
                confidence_score=0.0,
                reasoning="",
                balance_deduction_amount=0,
                balance_deduction_reason="",
                order_intent=False,
                order_details=None,
                ai_config=ai_config
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

            logger.info(
                f"Balance deducted successfully: {deduction_amount} rupiah. New balance: {balance_result['new_balance']}")

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
            ai_config = state.get("ai_config")

            logger.info(f"Processing order with message: {message_text}")

            # Get LLM instance based on AI config
            llm = self._get_llm_for_config(ai_config)

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

            response = llm.invoke([
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
            if any(indonesian in state.get("message_text", "").lower() for indonesian in
                   ["saya", "mau", "bisa", "ingin"]):
                state[
                    "response_message"] = "Terima kasih atas pesan Anda! Kami telah menerima permintaan Anda dan akan segera menghubungi Anda."
            else:
                state[
                    "response_message"] = "Thank you for your message! We have received your request and will contact you shortly."
            state["order_details"] = "Order request received"
            return state
