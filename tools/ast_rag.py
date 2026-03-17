import ast
import os
from pathlib import Path
from pydantic import BaseModel, Field
import chromadb
from chromadb.utils import embedding_functions  # <-- We are importing this now

from tools.base import Tool, ToolInvocation, ToolResult, ToolKind
from config.config import Config

class ASTRagParams(BaseModel):
    query: str = Field(..., description="The semantic concept or feature you want to search the codebase for (e.g., 'database connection logic').")

class ASTRagTool(Tool):
    name = "search_ast"
    description = "Searches the codebase abstract syntax tree (AST) for semantic meaning. Use this instead of grep when you need to understand conceptual logic or find specific functions/classes by their intent rather than exact variable names."
    kind = ToolKind.READ
    schema = ASTRagParams

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        print("\n[AST-RAG] Booting local ChromaDB vector engine...")
        
        self.client = chromadb.PersistentClient(path="./chroma_ast_db")
        
        # --- THE FIX: Force Chroma to use the SentenceTransformer we already downloaded! ---
        self.emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        
        # Pass the embedding function into the collection
        self.collection = self.client.get_or_create_collection(
            name="codebase_ast",
            embedding_function=self.emb_fn
        )
        self._is_indexed = False

    def _index_codebase(self, workspace_dir: Path):
        print(f"\n[AST-RAG] Indexing AST for {workspace_dir}...")
        
        chunk_ids = []
        documents = []
        metadatas = []
        chunk_count = 0
        
        for root, _, files in os.walk(workspace_dir):
            if any(bad in root for bad in ["venv", ".git", "__pycache__", ".config", "chroma_ast_db"]):
                continue
                
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            source_code = f.read()
                        
                        tree = ast.parse(source_code)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                                chunk_code = ast.get_source_segment(source_code, node)
                                if not chunk_code:
                                    continue
                                
                                documents.append(chunk_code)
                                metadatas.append({
                                    "file_path": str(file_path),
                                    "type": type(node).__name__,
                                    "name": node.name
                                })
                                chunk_ids.append(f"chunk_{chunk_count}")
                                chunk_count += 1
                                
                    except Exception:
                        pass # Ignore broken files
        
        if documents:
            self.collection.upsert(
                documents=documents,
                metadatas=metadatas,
                ids=chunk_ids
            )
            print(f"[AST-RAG] Indexed {chunk_count} code chunks into ChromaDB.")
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
                
            formatted_results = "### AST-RAG SEMANTIC MATCHES ###\n"
            for i in range(len(results['documents'][0])):
                meta = results['metadatas'][0][i]
                code = results['documents'][0][i]
                
                formatted_results += f"\n--- {meta['type']}: {meta['name']} (File: {meta['file_path']}) ---\n"
                formatted_results += f"{code}\n"
                
            return ToolResult.success_result(formatted_results)
            
        except Exception as e:
            return ToolResult.error_result(f"ChromaDB Vector search failed: {str(e)}")