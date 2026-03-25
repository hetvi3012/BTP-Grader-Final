import streamlit as st
import asyncio
import os
import json
from pathlib import Path
import zipfile
import io
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from config.loader import load_config
from agent.agent import Agent
from agent.events import AgentEventType

# --- 1. NEW FUNCTION: AI Rubric Generator ---
async def auto_generate_rubric(question_text: str) -> str:
    config = load_config(cwd=Path.cwd())
    config.model_name = os.environ.get("MODEL_NAME", "llama-3.3-70b-versatile")
    
    prompt = f"""
    You are an expert Senior Computer Science Professor.
    I will give you an assignment question. Your job is to generate a strict, highly detailed grading rubric for it.
    Break it down into specific constraints, edge cases, and algorithmic requirements.
    
    Assignment Question:
    {question_text}
    
    Output ONLY the raw rubric text. Do not use XML tags. Format it cleanly with bullet points. Do not include greetings or conversational filler.
    """
    
    raw_text = ""
    async with Agent(config) as agent:
        # Blindfold the agent
        agent.session.tool_registry.get_tools = lambda: []
        
        async for event in agent.run(prompt):
            if event.type == AgentEventType.TEXT_DELTA:
                raw_text += event.data.get("content", "")
            elif event.type == AgentEventType.TEXT_COMPLETE:
                if not raw_text:
                    raw_text = event.data.get("content", "")
                    
    return raw_text.strip()

# --- 2. EXISTING FUNCTION: Student Code Grader ---
async def grade_student_code(agent: Agent, filename: str, student_code: str, rubric_text: str, system_prompt: str) -> dict:
    prompt = f"""
    {system_prompt}
    
    <rubric>
    {rubric_text}
    </rubric>
    
    CRITICAL INSTRUCTION: DO NOT USE ANY TOOLS OR CALL ANY FUNCTIONS. 
    The student's code is provided completely in the text below. Read it directly.
    
    <student_submission filename="{filename}">
    {student_code}
    </student_submission>
    
    Execute your grading task and output ONLY the valid JSON.
    """
    
    raw_text = ""
    async for event in agent.run(prompt):
        if event.type == AgentEventType.AGENT_ERROR:
            return {"error": event.data.get("error", "Unknown Error")}
        elif event.type == AgentEventType.TEXT_DELTA:
            raw_text += event.data.get("content", "")
        elif event.type == AgentEventType.TEXT_COMPLETE:
            if not raw_text:
                raw_text = event.data.get("content", "")
                
    if not raw_text:
        return {"error": "Agent returned empty response."}

    raw_text = raw_text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
    elif raw_text.startswith("```"):
        raw_text = raw_text.replace("```", "").strip()
        
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"error": "LLM failed to output valid JSON", "raw_output": raw_text}

async def process_files(uploaded_files, rubric_text, system_prompt):
    config = load_config(cwd=Path.cwd())
    config.model_name = os.environ.get("MODEL_NAME", "llama-3.3-70b-versatile")
    
    results = {}
    
    async with Agent(config) as agent:
        agent.session.tool_registry.get_tools = lambda: []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, file in enumerate(uploaded_files):
            status_text.text(f"Grading {file.name}... ({i+1}/{len(uploaded_files)})")
            code = file.getvalue().decode("utf-8")
            report = await grade_student_code(agent, file.name, code, rubric_text, system_prompt)
            results[file.name] = report
            progress_bar.progress((i + 1) / len(uploaded_files))
            
        status_text.text("Grading Complete!")
        return results

# --- STREAMLIT UI LAYOUT ---
st.set_page_config(page_title="AI Bulk Grader", page_icon="📝", layout="wide")

st.title("📝 Automated B.Tech Code Grader")
st.markdown("Upload Python submissions to instantly generate structured JSON evaluation reports using a 70B parameter LLM.")

# Load memory assets
persona_path = Path("commands/grader.md")
system_prompt_default = persona_path.read_text() if persona_path.exists() else "You are an AI Grader..."

rubric_path = Path("memory/rubric.md")
rubric_default = rubric_path.read_text() if rubric_path.exists() else "1. Code must run."

# Initialize Session State for the Rubric
# Initialize Session State for the Rubric
if "rubric_editor" not in st.session_state:
    st.session_state.rubric_editor = rubric_default

# --- INTERACTIVE SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configuration Editor")
    
    st.subheader("1. AI Rubric Generator")
    st.info("Paste the plain English assignment question here, and the AI will generate a strict rubric for you.")
    assignment_q = st.text_area("Plain English Question:", height=100)
    
    if st.button("✨ Auto-Generate Rubric"):
        if assignment_q:
            with st.spinner("Generating rigorous rubric..."):
                generated_text = asyncio.run(auto_generate_rubric(assignment_q))
                # 1. Save the new text directly into the text area's memory key
                st.session_state.rubric_editor = generated_text
                # 2. Force Streamlit to refresh the UI immediately to show the text!
                st.rerun() 
        else:
            st.warning("Please enter a question first.")
    
    st.subheader("2. Review & Edit Rubric")
    # By using ONLY the 'key' argument, Streamlit handles the state automatically
    final_rubric = st.text_area(
        "Final Grading Constraints:", 
        height=300,
        key="rubric_editor" 
    )
    
    with st.expander("Advanced: Edit AI Persona"):
        system_prompt = st.text_area("Grader Instructions:", value=system_prompt_default, height=300)

# --- MAIN UI AREA ---
 

# --- MAIN UI AREA ---
uploaded_files = st.file_uploader("Upload Student Submissions (.py, .cpp, .java)", accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Start Bulk Grading", type="primary"):
        with st.spinner("Initializing AI Engine..."):
            # Pass the finalized rubric to the grader
            reports = asyncio.run(process_files(uploaded_files, st.session_state.rubric_editor, system_prompt))            
            st.success("All files processed successfully!")
            
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for filename, report in reports.items():
                    json_str = json.dumps(report, indent=4)
                    zip_file.writestr(f"{filename.split('.')[0]}_report.json", json_str)
                    
                    with st.expander(f"📄 Report: {filename}"):
                        st.json(report)
            
            st.download_button(
                label="📥 Download All Reports (ZIP)",
                data=zip_buffer.getvalue(),
                file_name="grading_reports.zip",
                mime="application/zip",
                type="primary"
            )