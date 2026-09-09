"""
retrieve_and_generate Lambda function.

Implements an agentic RAG pipeline using Strands Agents graph pattern:

Graph: intent_detector → retrieval_agent → response_generator

1. Intent Detector (Nova Micro): Validates user input for harmful content
2. Retrieval Agent (Nova Lite): Queries Bedrock KB (with rephrase retry + LLM fallback)
3. Response Generator (Nova Pro): Formats final response (tables, PDF/DOCX generation)

Triggered by API Gateway (POST /chat) with Lambda Proxy integration.
Uses the official Strands Agents Lambda Layer.
"""

import json
import os

import boto3
from botocore.config import Config
from strands import Agent, tool
from strands.multiagent import GraphBuilder

# --- Environment Variables ---
KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
FOUNDATION_MODEL_ARN = os.environ["FOUNDATION_MODEL_ARN"]

# --- AWS Clients (with adaptive retry for throttling resilience) ---
retry_config = Config(retries={"max_attempts": 3, "mode": "adaptive"})
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", config=retry_config)
bedrock_runtime = boto3.client("bedrock-runtime", config=retry_config)


# =============================================================
# TOOLS
# =============================================================

@tool
def retrieve_from_knowledge_base(query: str) -> str:
    """Search the Bedrock Knowledge Base for information relevant to the query.

    Use this tool to find information from uploaded documents in the knowledge base.

    Args:
        query: The search query to find relevant information
    """
    try:
        result = bedrock_agent_runtime.retrieve_and_generate(
            input={"text": query},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                    "modelArn": FOUNDATION_MODEL_ARN,
                },
            },
        )
        output_text = result["output"]["text"]

        # Use citations as deterministic signal for KB relevance
        citations = result.get("citations", [])
        has_citations = any(
            citation.get("retrievedReferences", [])
            for citation in citations
        )

        if not has_citations:
            return f"NO_KB_RESULT: {output_text}"

        return f"KB_RESULT: {output_text}"

    except Exception as e:
        return f"KB_ERROR: {str(e)}"


# =============================================================
# AGENT DEFINITIONS & GRAPH (initialized once at module level)
# =============================================================

def _create_graph():
    """Build the agent graph: intent_detector → retrieval_agent → response_generator.
    
    Uses different models per agent based on task complexity:
    - Intent detector: Nova Micro (fastest, cheapest - simple classification)
    - Retrieval agent: Nova Lite (medium - tool calling + reasoning)
    - Response generator: Nova Pro (most capable - formatting + doc generation)
    """

    intent_detector = Agent(
        name="intent_detector",
        system_prompt="""You are a content safety filter. Analyze the user's input for harmful content.

Check for: hate speech, violence instructions, illegal activities, sexually explicit content, self-harm, harassment.

Respond with ONLY one of these two formats:
- SAFE: [repeat the original input verbatim]
- UNSAFE: [brief reason]

Nothing else.""",
        model="us.amazon.nova-lite-v1:0",
    )

    retrieval_agent = Agent(
        name="retrieval_agent",
        system_prompt="""You are a retrieval specialist. Find relevant information from the knowledge base.

You have the retrieve_from_knowledge_base tool. Follow this strategy:
1. Search with the original question
2. If result starts with "NO_KB_RESULT" or "KB_ERROR", rephrase the question semantically and try once more
3. If the second attempt also returns "NO_KB_RESULT" or "KB_ERROR", respond using your general knowledge but prefix with: "NOTE: No relevant information was found in the knowledge base. Based on general knowledge: "
4. If result starts with "KB_RESULT", return the information directly

Be concise. Return only the information.""",
        tools=[retrieve_from_knowledge_base],
        model="us.amazon.nova-lite-v1:0",
    )

    response_generator = Agent(
        name="response_generator",
        system_prompt="""You are a professional response formatter. You receive retrieved information and must format it into a clear final response for the user.

SOURCE ATTRIBUTION RULES (strictly follow):
- If the input contains "KB_RESULT" or does NOT contain "NOTE: No relevant information was found", the data comes FROM THE KNOWLEDGE BASE. Do NOT add any disclaimers. Present the information confidently.
- If the input contains "NOTE: No relevant information was found in the knowledge base. Based on general knowledge:", the data comes from GENERAL KNOWLEDGE. In this case, start your response with: "Based on general knowledge (not from your uploaded documents):" and then present the information.
- Never mix these two — the source is always one or the other, never both.
- Never say "the knowledge base could not provide sufficient information" if information WAS provided.

FORMATTING RULES:
- Use markdown tables (| col1 | col2 |) when data is tabular
- Use bullet points for lists
- Use **bold** for key points
- Keep responses concise and well-organized
- Do NOT add meta-commentary about the retrieval process itself

Never fabricate information. Only format what was provided to you.""",
        model="us.amazon.nova-pro-v1:0",
    )

    builder = GraphBuilder()
    builder.add_node(intent_detector, "intent_detector")
    builder.add_node(retrieval_agent, "retrieval_agent")
    builder.add_node(response_generator, "response_generator")

    builder.add_edge("intent_detector", "retrieval_agent", condition=_is_safe_input)
    builder.add_edge("retrieval_agent", "response_generator")

    builder.set_entry_point("intent_detector")
    builder.set_execution_timeout(55)

    return builder.build()


def _is_safe_input(state):
    """Condition: proceed unless intent detector explicitly marked input as UNSAFE.
    
    Fail-open design: if the LLM response doesn't contain 'UNSAFE', we proceed.
    This avoids blocking legitimate queries due to unexpected LLM formatting.
    """
    intent_result = state.results.get("intent_detector")
    if not intent_result:
        return True  # Fail-open: if no result, proceed anyway
    result_text = str(intent_result.result).upper()
    # Block ONLY if explicitly marked unsafe
    return "UNSAFE:" not in result_text


# Initialize graph once at module level (reused across warm invocations)
# This saves ~500ms per request by avoiding repeated Agent/Graph construction
_graph = _create_graph()


# =============================================================
# LAMBDA HANDLER
# =============================================================

def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
        query = body.get("query", "").strip()

        if not query:
            return response(400, {"error": "query is required"})

        # Execute the pre-built agent graph
        result = _graph(query)

        # Extract the final response
        final_output = _extract_response(result)
        return response(200, final_output)

    except TimeoutError:
        print("Graph execution timed out")
        return response(200, {
            "answer": "The query is taking longer than expected. Please try a shorter or simpler question.",
        })
    except Exception as e:
        print(f"Error in agentic RAG: {e}")
        return response(200, {
            "answer": "I encountered an issue processing your request. Please try rephrasing your question.",
        })


def _extract_response(graph_result):
    """Extract the final response from the graph execution result."""
    output = {"answer": ""}

    # Check if intent was blocked
    intent_result = graph_result.results.get("intent_detector")
    if intent_result:
        intent_text = str(intent_result.result).upper()
        if "UNSAFE:" in intent_text:
            reason = str(intent_result.result).split(":", 1)[-1].strip()
            output["answer"] = f"I'm sorry, I cannot process this request. {reason}"
            return output

    # Get the response generator output
    response_gen = graph_result.results.get("response_generator")
    if response_gen:
        output["answer"] = str(response_gen.result).strip()
    else:
        # Fallback: try retrieval agent result
        retrieval_result = graph_result.results.get("retrieval_agent")
        if retrieval_result:
            output["answer"] = str(retrieval_result.result)
        else:
            output["answer"] = "I was unable to process your request. Please try again."

    return output


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(body),
    }
