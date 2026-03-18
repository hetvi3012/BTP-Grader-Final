import os
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables FIRST so Ollama settings are read
load_dotenv()

# Import your existing configuration and agent systems
from config.loader import load_config
from agent.agent import Agent
from agent.events import AgentEventType
from persona_loader import load_persona

async def grade_student_file(agent: Agent, file_path: Path, rubric_text: str, system_prompt: str) -> dict:
    """Evaluates a single student file and extracts the JSON report."""
    print(f"[*] Grading {file_path.name}...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        student_code = f.read()

    # We inject the Persona and Rubric directly into the prompt
    # We inject the Persona and Rubric directly into the prompt
    prompt = f"""
    {system_prompt}
    
    {rubric_text}
    
    CRITICAL INSTRUCTION: DO NOT USE ANY TOOLS OR CALL ANY FUNCTIONS. 
    The student's code is provided completely in the text below. Read it directly.
    
    <student_submission filename="{file_path.name}">
    {student_code}
    </student_submission>
    
    Execute your grading task and output ONLY the valid JSON.
    """
    
    raw_text = ""
    
    # Listen to the Agent's event stream - ROBUST VERSION
    async for event in agent.run(prompt):
        # 1. Catch explicit errors (e.g., Ollama connection refused)
        if event.type == AgentEventType.AGENT_ERROR:
            error_msg = event.data.get("error", "Unknown Agent Error")
            print(f"\n[!] Agent Error: {error_msg}")
            return {"error": error_msg}
            
        # 2. Accumulate the text chunks word-by-word as they stream!
        elif event.type == AgentEventType.TEXT_DELTA:
            raw_text += event.data.get("content", "")
            
        # 3. Fallback (just in case the delta stream was empty but complete fired)
        elif event.type == AgentEventType.TEXT_COMPLETE:
            if not raw_text:
                raw_text = event.data.get("content", "")
            
    if not raw_text:
        print("\n[!] Debug: No text was generated. Make sure 'ollama serve' is running in another terminal.")
        return {"error": "Agent returned empty response."}

    # Clean the output (strip markdown backticks if the LLM adds them)
    raw_text = raw_text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
    elif raw_text.startswith("```"):
        raw_text = raw_text.replace("```", "").strip()
        
    try:
        report = json.loads(raw_text)
        return report
    except json.JSONDecodeError:
        print(f"\n[!] Warning: {file_path.name} returned invalid JSON.")
        print(f"--- RAW LLM OUTPUT ---\n{raw_text}\n----------------------")
        return {"error": "LLM failed to output valid JSON", "raw_output": raw_text}

async def main():
    config = load_config(cwd=Path.cwd())
    config.model_name = os.environ.get("MODEL_NAME", "llama3.1")
    # 1. Load the Persona and Rubric
    system_prompt = load_persona("grader")
    if not system_prompt:
        print("[!] Error: Could not load commands/grader.md")
        return
    
    rubric_path = Path("memory/rubric.md")
    rubric_text = rubric_path.read_text() if rubric_path.exists() else ""

    # 2. Setup Directories
    input_dir = Path("student_submissions")
    output_dir = Path("grading_reports")
    input_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    
    # 3. Boot the Agent and Grade
    # 3. Boot the Agent and Grade
    async with Agent(config) as agent:
        
        # [!] MONKEY-PATCH: Hide all tools from the Agent.
        # This prevents the 70B model from trying to execute student code in your terminal!
        agent.session.tool_registry.get_tools = lambda: []
        
        student_files = list(input_dir.glob("*.py"))
        
        if not student_files:
            print(f"No python files found in {input_dir}/. Create a test file there!")
            return
            
        print(f"Found {len(student_files)} submissions. Starting bulk run...\n")
        
        for sub_file in student_files:
            report_json = await grade_student_file(agent, sub_file, rubric_text, system_prompt)
            
            report_path = output_dir / f"{sub_file.stem}_report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_json, f, indent=4)
                
            print(f"[+] Saved report to: {report_path}\n")

if __name__ == "__main__":
    asyncio.run(main())