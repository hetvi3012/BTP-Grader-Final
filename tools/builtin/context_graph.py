import os
import networkx as nx
from pydantic import BaseModel, Field
from typing import Any, Dict

# Import the base classes from your architecture
from tools.base import Tool, ToolResult, ToolInvocation, ToolKind
# Import your new engine
from utils.graph_builder import PolyglotGraphBuilder

# 1. Define the Schema for the LLM
class ContextGraphParams(BaseModel):
    target_node: str = Field(
        ..., 
        description="The exact name of the function or class to investigate (e.g., 'process_message' or 'main')."
    )

# 2. Create the Tool Class
class ContextGraphTool(Tool):
    @property
    def name(self) -> str:
        return "context_graph_traversal"

    @property
    def description(self) -> str:
        return (
            "Traverses the codebase's Abstract Syntax Tree (AST) to find all multi-hop dependencies "
            "(helper functions) called by a specific target node. Use this tool BEFORE editing complex code "
            "to ensure you understand the execution flow and do not hallucinate missing dependencies."
        )

    @property
    def kind(self) -> ToolKind:
        # This is a read-only operation, so it won't trigger the Approval UI
        return ToolKind.READ

    @property
    def schema(self) -> Dict[str, Any]:
        return ContextGraphParams.model_json_schema()

    async def execute(self, invocation: 'ToolInvocation') -> 'ToolResult':
        try:
            # 1. Bulletproof parameter extraction (handles any typos made in base.py)
            raw_params = getattr(
                invocation, "parameters", 
                getattr(invocation, "params", 
                getattr(invocation, "args", 
                getattr(invocation, "parameter", {})))
            )
            
            # 2. Extract the target node safely (handling both 'target_node' and 'target' keys)
            target_node = raw_params.get("target_node") or raw_params.get("target")
            
            if not target_node:
                return ToolResult.error_result(
                    "Missing 'target_node' parameter. The LLM failed to provide the function name."
                )
            
            # The agent passes its current working directory automatically (safe extraction)
            cwd = str(getattr(invocation, "cwd", getattr(invocation, "current_working_directory", "")))
            
            # 3. Build the graph using your Tree-sitter engine
            builder = PolyglotGraphBuilder()
            graph = builder.build_graph(cwd)
            
            # 4. Check if the target exists
            if target_node not in graph:
                return ToolResult.error_result(
                    f"Node '{target_node}' not found in the Context Graph. Ensure you spelled the function name correctly."
                )
                
            # 5. Find all dependencies (Successors = immediate helpers, Descendants = all downstream helpers)
            # We will use descendants to get the full execution chain
            import networkx as nx
            descendants = list(nx.descendants(graph, target_node))
            
            if not descendants:
                return ToolResult.success_result(
                    output=f"Topological Trace: '{target_node}' has no downstream helper dependencies. It is a leaf node."
                )
                
            # 6. Format the output nicely for the LLM
            output_lines = [f"Topological Dependencies for '{target_node}':", "This function relies on the following components to execute:"]
            
            # 6. Format the output nicely for the LLM
            local_deps = []
            external_deps = []
            
            for dep in descendants:
                node_data = graph.nodes[dep]
                file_name = node_data.get("file")
                
                if file_name:
                    local_deps.append(f"- `{dep}` (File: {file_name})")
                else:
                    external_deps.append(f"`{dep}`")
            
            output_lines = [f"Topological Dependencies for '{target_node}':"]
            
            if local_deps:
                output_lines.append("\n=== LOCAL HELPER FUNCTIONS (ACTION REQUIRED) ===")
                output_lines.append("You MUST use the read_file tool on the following files:")
                output_lines.extend(local_deps)
            else:
                output_lines.append("\n=== LOCAL HELPER FUNCTIONS ===")
                output_lines.append("None. This function does not call any local helpers.")
                
            if external_deps:
                output_lines.append("\n=== EXTERNAL / SYSTEM LIBRARIES (IGNORE) ===")
                output_lines.append(", ".join(external_deps))
                
            final_output = "\n".join(output_lines)
            
            return ToolResult.success_result(
                output=final_output,
                metadata={"target": target_node, "dependency_count": len(descendants)}
            )
            
        except Exception as e:
            return ToolResult.error_result(f"Graph Traversal Failed: {str(e)}")