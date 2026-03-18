import os
from pathlib import Path

def load_persona(persona_name: str) -> str:
    """Loads a persona markdown file from the commands/ directory."""
    persona_path = Path("commands") / f"{persona_name}.md"
    
    if not persona_path.exists():
        print(f"[!] Error: Persona file not found at {persona_path}")
        return ""
        
    try:
        return persona_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"[!] Error reading persona: {e}")
        return ""