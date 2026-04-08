import os
import importlib
from pathlib import Path
from pydantic import BaseModel, Field
import chromadb
from chromadb.utils import embedding_functions

# 🚨 Modern Tree-sitter imports
from tree_sitter import Language, Parser

from tools.base import Tool, ToolInvocation, ToolResult, ToolKind
from config.config import Config

class ASTRagParams(BaseModel):
    query: str = Field(..., description="The semantic concept or feature you want to search the codebase for.")

class ASTRagTool(Tool):
    name = "search_ast"
    description = "Searches the codebase abstract syntax tree (AST) for semantic meaning across ANY programming language. Use this to find conceptual logic, functions, or classes."
    kind = ToolKind.READ
    schema = ASTRagParams

    # Map extensions to their modern pip package suffix (e.g. tree_sitter_{name})
    LANGUAGE_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c"
    }

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        print("\n[AST-RAG] Booting Modern Polyglot ChromaDB vector engine...")
        
        self.client = chromadb.PersistentClient(path="./chroma_ast_db")
        self.emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        self.collection = self.client.get_or_create_collection(
            name="polyglot_ast",
            embedding_function=self.emb_fn
        )
        self._is_indexed = False

    def _extract_ast_nodes(self, node, source_bytes: bytes) -> list:
        """Recursively hunts for classes, functions, and methods in any AST."""
        results = []
        node_type = node.type.lower()
        
        # Heuristic: If the node type contains these keywords, it's a structural block
        if any(keyword in node_type for keyword in ["function", "method", "class"]):
            
            name = "unknown_node"
            for child in node.children:
                if "identifier" in child.type.lower() or "name" in child.type.lower():
                    name = source_bytes[child.start_byte:child.end_byte].decode('utf-8', errors='ignore')
                    break
            
            content = source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')
            
            results.append({
                "type": node.type,
                "name": name,
                "content": content
            })
        
        for child in node.children:
            results.extend(self._extract_ast_nodes(child, source_bytes))
            
        return results

    def _index_codebase(self, workspace_dir: Path):
        print(f"\n[AST-RAG] Indexing Polyglot AST for {workspace_dir}...")
        
        chunk_ids = []
        documents = []
        metadatas = []
        chunk_count = 0
        
        for root, _, files in os.walk(workspace_dir):
            # 🚨 ADD "lib" and "static" to this list!
            if any(bad in root for bad in ["venv", ".git", "__pycache__", "node_modules", "chroma_ast_db", "lib", "static"]):
                continue
                
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                
                if ext in self.LANGUAGE_MAP:
                    lang_name = self.LANGUAGE_MAP[ext]
                    file_path = os.path.join(root, file)
                    
                    try:
                        # 1. Dynamically import the official grammar module
                        try:
                            ts_module = importlib.import_module(f"tree_sitter_{lang_name}")
                        except ModuleNotFoundError:
                            print(f"[AST-RAG] Missing grammar for {ext}. Please run: pip install tree-sitter-{lang_name}")
                            continue

                        # 2. Modern Tree-sitter >= 0.22 instantiation
                        language = Language(ts_module.language())
                        parser = Parser(language)
                        
                        # 3. Read bytes and parse
                        with open(file_path, "rb") as f:
                            source_bytes = f.read()
                            
                        tree = parser.parse(source_bytes)
                        extracted_nodes = self._extract_ast_nodes(tree.root_node, source_bytes)
                        
                        for node_data in extracted_nodes:
                            if not node_data["content"].strip():
                                continue
                                
                            documents.append(node_data["content"])
                            metadatas.append({
                                "file_path": str(file_path),
                                "language": lang_name,
                                "type": node_data["type"],
                                "name": node_data["name"]
                            })
                            chunk_ids.append(f"chunk_{chunk_count}")
                            chunk_count += 1
                            
                    except Exception as e:
                        print(f"[AST-RAG] Failed to parse {file}: {e}")
                        pass
        
        if documents:
            batch_size = 5000  # Safe limit below ChromaDB's 5461 maximum
            
            for i in range(0, len(documents), batch_size):
                end_idx = i + batch_size
                self.collection.upsert(
                    documents=documents[i:end_idx],
                    metadatas=metadatas[i:end_idx],
                    ids=chunk_ids[i:end_idx]
                )
                
            print(f"[AST-RAG] Indexed {chunk_count} multi-language AST chunks into ChromaDB.")
        self._is_indexed = True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        query = invocation.params["query"]
        
        if not self._is_indexed:
            self._index_codebase(invocation.cwd)

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=3
            )
            
            if not results['documents'] or not results['documents'][0]:
                return ToolResult.success_result("No relevant semantic code found in the AST.")
                
            formatted_results = "### POLYGLOT AST-RAG SEMANTIC MATCHES ###\n"
            for i in range(len(results['documents'][0])):
                meta = results['metadatas'][0][i]
                code = results['documents'][0][i]
                
                formatted_results += f"\n--- {meta['type']}: {meta['name']} (Language: {meta['language']}, File: {meta['file_path']}) ---\n"
                formatted_results += f"{code}\n"
                
            return ToolResult.success_result(formatted_results)
            
        except Exception as e:
            return ToolResult.error_result(f"ChromaDB Vector search failed: {str(e)}")