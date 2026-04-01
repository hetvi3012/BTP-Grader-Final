import os
import networkx as nx
from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_python
import tree_sitter_javascript

# 1. Load the Language Grammars
LANGUAGES = {
    ".py": Language(tree_sitter_python.language()),
    ".js": Language(tree_sitter_javascript.language()),
}

# 2. Define the Query Patterns
QUERIES = {
    ".py": """
        (function_definition name: (identifier) @definition.function)
        (call function: (identifier) @call.function)
    """,
    ".js": """
        (function_declaration name: (identifier) @definition.function)
        (call_expression function: (identifier) @call.function)
    """
}

class PolyglotGraphBuilder:
    def __init__(self):
        self.graph = nx.DiGraph()

    def build_graph(self, workspace_dir: str) -> nx.DiGraph:
        # Ignore virtual environments and git folders
        ignore_dirs = {'venv', '.venv', 'env', 'node_modules', '.git', '__pycache__'}

        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                ext = os.path.splitext(file)[1]
                if ext in LANGUAGES:
                    file_path = os.path.join(root, file)
                    self._parse_file(file_path, ext)
        return self.graph

    def _parse_file(self, filepath: str, ext: str):
        language = LANGUAGES[ext]
        parser = Parser(language)

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read().encode("utf-8")
        except Exception:
            return

        tree = parser.parse(code)
        query = Query(language, QUERIES[ext])
        cursor = QueryCursor(query)
        captures_dict = cursor.captures(tree.root_node)

        all_captures = []
        for capture_name, nodes in captures_dict.items():
            for node in nodes:
                all_captures.append((node.start_byte, node, capture_name))
        
        all_captures.sort(key=lambda x: x[0])

        current_def = None
        # Ignore these common built-ins so they don't tangle the graph
        ignore_builtins = {'print', 'len', 'isinstance', 'type', 'str', 'int', 'list', 'dict', 'set', 'append', 'join', 'get', 'format', 'open'}

        for _, node, capture_name in all_captures:
            node_text = node.text.decode("utf8")
            
            if capture_name == "definition.function":
                current_def = node_text
                self.graph.add_node(current_def, type="function", file=os.path.basename(filepath))
                
            elif capture_name == "call.function" and current_def:
                callee_name = node_text
                if callee_name not in ignore_builtins:
                    self.graph.add_edge(current_def, callee_name, relation="CALLS")

    def export_focused_subgraph(self, target_node: str, depth=1, output_file="clean_subgraph.html"):
        """Exports ONLY the target node and its immediate neighbors."""
        from pyvis.network import Network
        
        if target_node not in self.graph:
            print(f"\n❌ ERROR: Function '{target_node}' not found in the codebase.")
            print("Try checking the exact spelling.")
            return

        # Extract only the neighborhood (1-hop by default)
        subgraph = nx.ego_graph(self.graph, target_node, radius=depth)
        
        net = Network(notebook=False, directed=True, height="100vh", width="100%", bgcolor="#222222", font_color="white")
        net.from_nx(subgraph)
        
        # Highlight the main target node in bright RED
        for node in net.nodes:
            if node["id"] == target_node:
                node["color"] = "#ff4b4b" 
                node["size"] = 40
                node["font"] = {"size": 25, "bold": True}
        
        net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=200)
        net.write_html(output_file)
        print(f"\n✅ SUCCESS! Clean sub-graph saved to {output_file}")


# ==========================================
# SCRIPT EXECUTION
# ==========================================
if __name__ == "__main__":
    print("Parsing codebase... (ignoring venv and built-ins)")
    builder = PolyglotGraphBuilder()
    builder.build_graph(".") 
    
    # 🛑 THE MAGIC TARGET 🛑
    # Put the exact name of a function you want to see here
    target = "_get_environment_section" 
    
    print(f"Extracting focused sub-graph for '{target}'...")
    builder.export_focused_subgraph(target_node=target, depth=1)