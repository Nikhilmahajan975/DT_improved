"""
Dynatrace AI Assistant - Main Application
Enhanced with AI-powered intent understanding for natural conversations
"""
import streamlit as st
from datetime import datetime
from config.settings import config
from utils.logger import setup_logger
from utils.timeframe import human_readable_timeframe
from prompt_handler.intent_parser import AIIntentParser
from dynatrace_api.services import DynatraceServicesAPI
from dynatrace_api.metrics import DynatraceMetricsAPI
from dynatrace_api.problems import DynatraceProblemsAPI
from llm.response_generator import AIResponseGenerator

logger = setup_logger(__name__)

# Initialize components
services_api = DynatraceServicesAPI()
metrics_api = DynatraceMetricsAPI()
problems_api = DynatraceProblemsAPI()
ai_generator = AIResponseGenerator()

# Initialize AI-powered intent parser
intent_parser = AIIntentParser(ai_client=ai_generator)

def initialize_session_state():
    """Initialize Streamlit session state for chat history"""
    if "messages" not in st.session_state:
        st.session_state.messages = []
        # Add personalized welcome message
        welcome_msg = (
            "👋 Hi! I'm your Dynatrace AI Assistant. I can help you with:\n\n"
            "• **Check service health**: Just ask \"How is my ordercontroller doing?\"\n"
            "• **List services**: Say \"Show me all my services\"\n"
            "• **Analyze metrics**: Ask \"What's the performance of checkout-service?\"\n"
            "• **Troubleshoot issues**: Try \"Why is payment-api slow?\"\n\n"
            "Go ahead, ask me anything in your own words! 😊"
        )
        st.session_state.messages.append({
            "role": "assistant",
            "content": welcome_msg,
            "timestamp": datetime.now()
        })
    
    # Track conversation context for better understanding
    if "conversation_context" not in st.session_state:
        st.session_state.conversation_context = {
            "last_service": None,
            "last_intent": None,
            "last_timeframe": "2h"
        }

def add_message(role: str, content: str):
    """Add a message to chat history"""
    st.session_state.messages.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now()
    })
    
    if len(st.session_state.messages) > config.MAX_CHAT_HISTORY:
        # Keep welcome message + recent messages
        welcome = st.session_state.messages[0]
        recent = st.session_state.messages[-(config.MAX_CHAT_HISTORY-1):]
        st.session_state.messages = [welcome] + recent

def update_context(intent: dict):
    """Update conversation context for follow-up questions"""
    if intent.get("service_name"):
        st.session_state.conversation_context["last_service"] = intent["service_name"]
    if intent.get("type"):
        st.session_state.conversation_context["last_intent"] = intent["type"]
    if intent.get("timeframe"):
        st.session_state.conversation_context["last_timeframe"] = intent["timeframe"]

def apply_context(intent: dict) -> dict:
    """Apply conversation context to incomplete queries"""
    context = st.session_state.conversation_context
    
    # If no service specified but we have context, ask if they meant the same service
    if not intent.get("service_name") and context.get("last_service"):
        # For follow-up questions, assume same service
        if intent.get("type") == context.get("last_intent"):
            intent["service_name"] = context["last_service"]
            intent["is_followup"] = True
    
    # Apply last timeframe if not specified
    if not intent.get("timeframe") or intent.get("timeframe") == "2h":
        if context.get("last_timeframe"):
            intent["timeframe"] = context["last_timeframe"]
    
    return intent

def handle_check_abnormality(intent: dict) -> str:
    """Handle abnormality check requests"""
    service_name = intent.get("service_name")
    timeframe = intent.get("timeframe", "2h")
    
    if not service_name:
        # Provide helpful suggestion
        recent_services = get_recent_services_from_history()
        if recent_services:
            return (
                "I'd be happy to check a service for you! Which service would you like me to analyze?\n\n"
                f"Recently mentioned: {', '.join(recent_services)}\n\n"
                "Or you can say 'show all services' to see the full list."
            )
        else:
            return (
                "I'd be happy to check a service for you! Please tell me which service to analyze.\n\n"
                "For example: 'Check ordercontroller' or 'How is payment-api doing?'"
            )
    
    with st.spinner(f"🔍 Analyzing {service_name}..."):
        # Get entity ID
        entity_id = services_api.get_service_entity_id(service_name)
        
        if not entity_id:
            # Try to find similar service names
            similar = find_similar_services(service_name)
            if similar:
                return (
                    f"❌ I couldn't find a service called '{service_name}'.\n\n"
                    f"Did you mean one of these?\n" + 
                    "\n".join([f"• {s}" for s in similar[:3]])
                )
            else:
                return (
                    f"❌ I couldn't find a service called '{service_name}'.\n\n"
                    "Try 'show all services' to see what's available, or check the spelling."
                )
        
        # Fetch data - pass entity_id for proper problem correlation
        problems = problems_api.get_problems_for_service(service_name, entity_id, timeframe)
        metrics = metrics_api.get_service_metrics(entity_id, timeframe)
        insights = metrics_api.analyze_metrics(metrics)
        
        # Display metrics in UI
        display_metrics_ui(service_name, metrics, problems, insights)
        
        # Generate AI response with context
        context_note = ""
        if intent.get("is_followup"):
            context_note = " (following up on previous query)"
        
        response = ai_generator.generate_service_analysis(
            service_name=service_name,
            metrics=metrics,
            problems=problems,
            insights=insights,
            timeframe=human_readable_timeframe(timeframe)
        )
        
        return response

def handle_list_services(intent: dict) -> str:
    """Handle service listing requests"""
    with st.spinner("📋 Fetching services..."):
        services = services_api.list_services(limit=50)
        
        if not services:
            return "❌ I couldn't retrieve the service list. Please check your Dynatrace connection."
        
        # Group by type for better readability
        services_by_type = {}
        for service in services:
            service_type = service.get("properties", {}).get("serviceType", "Unknown")
            if service_type not in services_by_type:
                services_by_type[service_type] = []
            services_by_type[service_type].append(
                service.get("displayName", service.get("entityId", "Unknown"))
            )
        
        # Generate response
        response_parts = [f"I found **{len(services)}** services in your environment:\n"]
        
        for service_type, service_names in sorted(services_by_type.items()):
            response_parts.append(f"\n**{service_type}** ({len(service_names)} services):")
            for name in sorted(service_names[:10]):  # Limit per type
                response_parts.append(f"• {name}")
            if len(service_names) > 10:
                response_parts.append(f"  ... and {len(service_names) - 10} more")
        
        response_parts.append("\n💡 Ask me to check any of these services!")
        
        return "\n".join(response_parts)

def handle_general_question(intent: dict) -> str:
    """Handle general questions about the system"""
    query = intent.get("raw_query", "").lower()
    
    # Check for common questions
    if any(word in query for word in ['help', 'what can you do', 'how do i', 'commands']):
        return (
            "I can help you with several things:\n\n"
            "**🔍 Check Service Health**\n"
            "• \"How is ordercontroller doing?\"\n"
            "• \"Check issues with payment-api\"\n"
            "• \"Any problems with checkout-service in the last hour?\"\n\n"
            "**📋 List Services**\n"
            "• \"Show me all services\"\n"
            "• \"What services are available?\"\n\n"
            "**📊 Analyze Performance**\n"
            "• \"What's the performance of auth-service?\"\n"
            "• \"Show metrics for user-service\"\n\n"
            "**🔧 Troubleshoot**\n"
            "• \"Why is inventory-api slow?\"\n"
            "• \"Debug cart-service\"\n\n"
            "Just ask naturally - I understand conversational queries! 😊"
        )
    
    # Use AI to generate a helpful response
    if ai_generator.provider != 'fallback':
        try:
            prompt = f"""User asked: {query}

This is a Dynatrace monitoring chatbot. The user seems to have a general question.
Provide a helpful, brief response. If their question is unclear, politely ask for clarification
and give examples of what they can ask about."""
            
            # Simple AI call for general questions
            return ai_generator._call_openai("You are a helpful Dynatrace assistant.", prompt) if ai_generator.provider == 'openai' else \
                   ai_generator._call_anthropic("You are a helpful Dynatrace assistant.", prompt) if ai_generator.provider == 'anthropic' else \
                   ai_generator._call_gemini("You are a helpful Dynatrace assistant.", prompt) if ai_generator.provider == 'gemini' else \
                   "I'm not sure I understand. Could you rephrase that? Or type 'help' to see what I can do."
        except:
            pass
    
    return (
        "I'm not quite sure what you're asking. Here are some things I can help with:\n\n"
        "• Check service health: 'How is ordercontroller?'\n"
        "• List services: 'Show all services'\n"
        "• Analyze metrics: 'Performance of payment-api'\n\n"
        "What would you like to know?"
    )

def get_recent_services_from_history() -> list:
    """Extract service names mentioned in recent conversation"""
    services = []
    for msg in reversed(st.session_state.messages[-10:]):
        if msg["role"] == "user":
            # Simple extraction from message content
            content = msg["content"].lower()
            for word in content.split():
                if any(suffix in word for suffix in ['api', 'service', 'controller']):
                    services.append(word)
    return list(set(services))[:3]

def find_similar_services(service_name: str) -> list:
    """Find services with similar names"""
    try:
        all_services = services_api.list_services(limit=100)
        similar = []
        service_lower = service_name.lower()
        
        for service in all_services:
            name = service.get("displayName", "").lower()
            # Check if names are similar
            if service_lower in name or name in service_lower:
                similar.append(service.get("displayName"))
            # Check for partial matches
            elif any(part in name for part in service_lower.split('-')):
                similar.append(service.get("displayName"))
        
        return similar[:5]
    except:
        return []

def display_metrics_ui(service_name: str, metrics: dict, problems: list, insights: dict):
    """Display metrics using Streamlit components"""
    st.markdown(f"### 📊 Metrics for {service_name}")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        error_count = metrics.get("error_count", "N/A")
        st.metric(label="Error Count", value=error_count)
    
    with col2:
        response_time = metrics.get("response_time", "N/A")
        rt_display = f"{response_time} ms" if isinstance(response_time, (int, float)) else response_time
        st.metric(label="Response Time", value=rt_display)
    
    with col3:
        request_count = metrics.get("request_count", "N/A")
        st.metric(label="Request Count", value=request_count)
    
    with col4:
        failure_rate = metrics.get("failure_rate", "N/A")
        fr_display = f"{failure_rate}%" if isinstance(failure_rate, (int, float)) else failure_rate
        st.metric(label="Failure Rate", value=fr_display)
    
    if problems:
        with st.expander(f"🚨 {len(problems)} Problem(s) Detected", expanded=True):
            for i, problem in enumerate(problems[:5], 1):
                status_emoji = "🔴" if problem.get("status") == "OPEN" else "🟢"
                st.markdown(
                    f"{status_emoji} **{i}.** {problem.get('title', 'Unknown')} "
                    f"({problem.get('status', 'Unknown')})"
                )
    
    status = insights.get("status", "unknown")
    if status == "healthy":
        st.success("✅ Service is healthy")
    elif status == "warning":
        st.warning("⚠️ Service has warnings")
    elif status == "critical":
        st.error("🔴 Service has critical issues")

def process_user_input(user_input: str) -> str:
    """Process user input and generate response"""
    # Parse intent with AI
    intent = intent_parser.parse(user_input)
    
    if not intent:
        return "I'm having trouble understanding that. Could you rephrase? Or type 'help' to see what I can do."
    
    # Apply conversation context
    intent = apply_context(intent)
    
    # Update context for future queries
    update_context(intent)
    
    intent_type = intent.get("type")
    
    # Route to appropriate handler
    try:
        if intent_type == "check_abnormality":
            return handle_check_abnormality(intent)
        elif intent_type == "list_services":
            return handle_list_services(intent)
        elif intent_type == "service_details":
            return handle_check_abnormality(intent)
        elif intent_type == "metrics_analysis":
            return handle_check_abnormality(intent)
        elif intent_type == "general_question":
            return handle_general_question(intent)
        else:
            return (
                f"I understand you want to {intent_type.replace('_', ' ')}, "
                f"but that feature is still being developed. 🚧\n\n"
                "For now, I can:\n"
                "• Check service health\n"
                "• List all services\n"
                "• Analyze metrics\n\n"
                "What would you like to try?"
            )
    except Exception as e:
        logger.error(f"Error processing intent: {e}", exc_info=True)
        return (
            "Oops! I encountered an error while processing your request. 😕\n\n"
            "Please try rephrasing your question, or type 'help' to see what I can do."
        )

def main():
    """Main application entry point"""
    st.set_page_config(
        page_title="Dynatrace AI Assistant",
        page_icon="🔍",
        layout="wide"
    )
    
    st.title("🔍 Dynatrace AI Assistant")
    st.markdown("**Ask me anything in your own words!** I understand natural language. 💬")
    
    # Initialize session state
    initialize_session_state()
    
    # Sidebar
    with st.sidebar:
        st.markdown("### 💡 Tips")
        st.markdown("""
        **I understand natural language!**
        
        Just talk to me normally:
        - "How's ordercontroller doing?"
        - "Show me everything"
        - "What's wrong with payment-api?"
        - "Check my services"
        
        No need for exact commands! 😊
        """)
        
        st.markdown("### 🤖 AI Provider")
        provider = ai_generator.provider.title()
        st.info(f"Using: **{provider}**")
        
        if provider == "Fallback":
            st.warning("⚠️ No AI configured. Using basic responses.")
            st.markdown("[Setup FREE AI →](FREE_AI_ALTERNATIVES_GUIDE.md)")
        
        if st.button("🗑️ Clear Chat History"):
            st.session_state.messages = []
            st.session_state.conversation_context = {
                "last_service": None,
                "last_intent": None,
                "last_timeframe": "2h"
            }
            st.rerun()
    
    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Chat input
    if prompt := st.chat_input("Ask me anything... (e.g., 'How is my ordercontroller?')"):
        # Add user message
        add_message("user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Generate and display response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    response = process_user_input(prompt)
                    st.markdown(response)
                    add_message("assistant", response)
                except Exception as e:
                    error_msg = "I encountered an error. Please try again or rephrase your question."
                    logger.error(f"Error processing input: {e}", exc_info=True)
                    st.error(error_msg)
                    add_message("assistant", error_msg)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        st.error(f"Application Error: {str(e)}")
