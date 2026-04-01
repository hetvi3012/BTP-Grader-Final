import streamlit as st
import asyncio
import os
import json
import re
from pathlib import Path
import zipfile
import io
import textwrap
from dotenv import load_dotenv
from fpdf import FPDF

# Load environment variables
load_dotenv(override=True)

# ==========================================
# 🚨 GLOBAL GHOST KILLER 🚨
# Force the environment variables BEFORE importing the AI framework
# This prevents the framework from secretly reading old models from config.toml files
# ==========================================
model_override = os.environ.get("GROQ_MODEL_NAME", "llama-3.3-70b-versatile")
os.environ["MODEL_NAME"] = model_override
os.environ["MODEL"] = model_override
os.environ["LITELLM_MODEL"] = model_override
os.environ["OPENAI_MODEL_NAME"] = model_override

if os.environ.get("GROQ_API_KEY"):
    os.environ["API_KEY"] = os.environ.get("GROQ_API_KEY")
    os.environ["OPENAI_API_KEY"] = os.environ.get("GROQ_API_KEY")
    
if os.environ.get("GROQ_BASE_URL"):
    os.environ["BASE_URL"] = os.environ.get("GROQ_BASE_URL")
    os.environ["OPENAI_API_BASE"] = os.environ.get("GROQ_BASE_URL")

# NOW import the agent (it will be forced to use the variables we just set above)
from config.loader import load_config
from agent.agent import Agent
from agent.events import AgentEventType

# ==========================================
# 🚨 DECOUPLE FROM CLI PROMPT 🚨
# ==========================================
import prompts.system
prompts.system.get_system_prompt = lambda *args, **kwargs: """
You are a strict, backend Automated Grading API.
Your ONLY purpose is to evaluate student code based on a rubric and output STRICT, VALID JSON.
DO NOT use tools. DO NOT use markdown code blocks (like ```json).
DO NOT output conversational text, preambles, or postambles. ONLY output the raw JSON object.
"""

# ==========================================
# 1. PDF GENERATOR FUNCTION
# ==========================================
def safe_write(pdf, text, indent=0):
    """Helper function to cleanly wrap text and avoid FPDF page-width crashes."""
    clean_text = str(text).encode('latin-1', 'replace').decode('latin-1')
    for paragraph in clean_text.split('\n'):
        # Force wrap at 85 characters so long code snippets don't break the page
        lines = textwrap.wrap(paragraph, width=85, break_long_words=True)
        if not lines:
            pdf.ln(6)
            continue
        for line in lines:
            if indent > 0:
                pdf.cell(indent, 6, "") # Indentation spacing
            pdf.cell(0, 6, line, ln=True)

def create_pdf(filename: str, report: dict) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=15, top=15, right=15)
    
    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Automated Grading Report: {filename}", ln=True, align="C")
    pdf.ln(5)
    
    # Detected Language
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Detected Language: {report.get('detected_language', 'Unknown')}", ln=True)
    pdf.ln(2)
    
    # Overview
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Overview", ln=True)
    pdf.set_font("Helvetica", "", 12)
    safe_write(pdf, report.get("overview", "No overview provided."))
    pdf.ln(5)
    
    # Syntax / Compilation Errors
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Syntax & Compilation Errors", ln=True)
    syntax_errors = report.get("syntax_errors", [])
    if not syntax_errors:
        pdf.set_font("Helvetica", "I", 12)
        safe_write(pdf, "No syntax errors detected. The code compiles/runs.")
    else:
        for i, err in enumerate(syntax_errors, 1):
            pdf.set_font("Helvetica", "B", 12)
            safe_write(pdf, f"{i}. Issue: {err.get('issue', '')}")
            pdf.set_font("Helvetica", "", 12)
            safe_write(pdf, f"Fix: {err.get('fix', '')}", indent=5)
            pdf.ln(2)
    pdf.ln(5)
    
    # Logical Errors & Rubric Violations
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Logical Errors & Rubric Violations", ln=True)
    logical_errors = report.get("logical_errors", [])
    if not logical_errors:
        pdf.set_font("Helvetica", "I", 12)
        safe_write(pdf, "No logical errors detected. Excellent work!")
    else:
        for i, err in enumerate(logical_errors, 1):
            pdf.set_font("Helvetica", "B", 12)
            safe_write(pdf, f"{i}. Issue: {err.get('issue', '')}")
            pdf.set_font("Helvetica", "", 12)
            safe_write(pdf, f"Fix: {err.get('fix', '')}", indent=5)
            pdf.ln(2)
            
    # AI Scratchpad (TA Reference)
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, "--- AI Evaluation Scratchpad (TA Reference) ---", ln=True)
    pdf.set_font("Helvetica", "", 10)
    safe_write(pdf, report.get("scratchpad", "No scratchpad data."))

    return pdf.output()

# ==========================================
# 2. AI RUBRIC GENERATOR
# ==========================================
async def auto_generate_rubric(question_text: str) -> str:
    config = load_config(cwd=Path.cwd())
    
    # 🚨 Bypass Pydantic's frozen state to force the Groq model
    try:
        object.__setattr__(config, 'model_name', os.environ.get("MODEL_NAME"))
    except Exception:
        pass
    
    prompt = f"""
    You are an expert Senior Computer Science Professor.
    I will give you an assignment question. Your job is to generate a strict, highly detailed grading rubric for it.
    
    CRITICAL RULES FOR THE RUBRIC:
    1. ASSUME VALID INPUTS: Do not include points for basic input validation (e.g., checking if integer 'n' is within constraints) unless explicitly asked.
    2. FOCUS ON ALGORITHM: Heavily weigh the core algorithmic logic, edge cases, and data structures.
    3. NO CHATTER: Output ONLY the raw rubric text. Format it cleanly with bullet points.
    4. CONCRETE ALGORITHMIC FIXES: When explaining a Logical Error, your "fix" MUST contain concrete algorithmic steps, data structures, or code logic. FORBIDDEN PHRASES: "Modify the algorithm", "Add logic to check", "Fix the edge case". You must explain EXACTLY HOW to fix it (e.g., "Group buildings by row in a hash map, sort the coordinates, and check if the current building's coordinate is strictly between the min and max values of that row.").
    Assignment Question:
    {question_text}
    """
    
    raw_text = ""
    error_message = ""
    async with Agent(config) as agent:
        agent.session.tool_registry.get_tools = lambda: []
        async for event in agent.run(prompt):
            if event.type == AgentEventType.AGENT_ERROR:
                error_message = event.data.get("error", "Unknown API Error")
            elif event.type == AgentEventType.TEXT_DELTA:
                raw_text += event.data.get("content", "")
            elif event.type == AgentEventType.TEXT_COMPLETE:
                if not raw_text:
                    raw_text = event.data.get("content", "")
                    
    if error_message:
        return f"❌ ERROR CONNECTING TO GROQ API:\n{error_message}"
                    
    return raw_text.strip()

# ==========================================
# 3. STUDENT CODE GRADER
# ==========================================
async def grade_student_code(agent: Agent, filename: str, student_code: str, rubric_text: str, system_prompt: str) -> dict:
    
    # 1. Python-Assisted Language Detection
    ext = filename.split('.')[-1].lower()
    language_map = {
        "py": "Python", 
        "cpp": "C++", 
        "c": "C", 
        "java": "Java", 
        "js": "JavaScript"
    }
    hardcoded_language = language_map.get(ext, "Unknown")

# 🚨 REPLACE YOUR EXISTING PROMPT VARIABLE WITH THIS ONE 🚨
    prompt = f"""
    {system_prompt}
    
    <exam_rubric>
    {rubric_text}
    </exam_rubric>
    
    CRITICAL INSTRUCTIONS FOR MULTI-QUESTION EXAMS:
    1. Look at the FILENAME provided below. It usually indicates which question the student is answering (e.g., 'q1.c', 'prob3.py', 'Q4_palindrome.c').
    2. Identify which specific question from the <exam_rubric> this file corresponds to.
    3. Grade this code ONLY against the criteria for THAT SPECIFIC QUESTION. Completely ignore the rubric criteria for the other questions.
    4. If the code does not seem to match the question implied by the filename, grade the code based on what it actually attempts to solve from the rubric.
    
    DO NOT USE ANY TOOLS OR CALL ANY FUNCTIONS. 
    Read the file context and code below, then output ONLY the valid JSON.
    
    FILE CONTEXT:
    - Filename: {filename}
    - Language: {hardcoded_language}
    
    STUDENT CODE:
    ```{ext}
    {student_code}
    ```
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
    
    # Bulletproof Regex JSON Extraction
    json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if json_match:
        raw_text = json_match.group(0)
        
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"error": "LLM failed to output valid JSON", "raw_output": raw_text}

# ==========================================
# 4. ZIP EXTRACTOR HELPER
# ==========================================
def extract_valid_files(uploaded_files):
    """Processes uploaded files, unzipping archives in memory and extracting source code."""
    extracted_submissions = []
    allowed_extensions = {".py", ".cpp", ".c", ".java", ".js"}

    for file in uploaded_files:
        if file.name.lower().endswith('.zip'):
            # Open the ZIP file in memory
            with zipfile.ZipFile(file, 'r') as z:
                for file_info in z.infolist():
                    # Skip directories and macOS hidden junk folders
                    if file_info.is_dir() or '__MACOSX' in file_info.filename:
                        continue
                    
                    ext = os.path.splitext(file_info.filename)[1].lower()
                    if ext in allowed_extensions:
                        # Read the file content
                        raw_bytes = z.read(file_info.filename)
                        content = raw_bytes.decode('utf-8', errors='replace')
                        
                        # Create a clear filename: "ZIPNAME_FILENAME"
                        student_id = file.name.replace('.zip', '')
                        clean_filename = f"{student_id}_{os.path.basename(file_info.filename)}"
                        
                        extracted_submissions.append({
                            "name": clean_filename,
                            "content": content
                        })
        else:
            # It's a standard loose file, process normally
            ext = os.path.splitext(file.name)[1].lower()
            if ext in allowed_extensions:
                extracted_submissions.append({
                    "name": file.name,
                    "content": file.getvalue().decode('utf-8', errors='replace')
                })
                
    return extracted_submissions

# ==========================================
# 5. BULK PROCESSING ORCHESTRATOR
# ==========================================
# ==========================================
# 5. BULK PROCESSING ORCHESTRATOR
# ==========================================
async def process_files(uploaded_files, rubric_text, system_prompt):
    config = load_config(cwd=Path.cwd())
    
    # Bypass Pydantic's frozen state to force the Groq model
    try:
        object.__setattr__(config, 'model_name', os.environ.get("MODEL_NAME"))
    except Exception:
        pass
    
    results = {}
    
    # Extract files from ZIPs and loose files
    submissions_to_grade = extract_valid_files(uploaded_files)
    
    if not submissions_to_grade:
        st.error("No valid code files (.c, .cpp, .py, .java, .js) found in the uploads!")
        return results
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_files = len(submissions_to_grade)
    
    for i, submission in enumerate(submissions_to_grade):
        status_text.text(f"Grading {submission['name']}... ({i+1}/{total_files})")
        
        # 🚨 FIX 1: Create a BRAND NEW agent with a wiped memory for EVERY student
        async with Agent(config) as agent:
            # Force disable tools for the grader
            agent.session.tool_registry.get_tools = lambda: []
            
            report = await grade_student_code(agent, submission['name'], submission['content'], rubric_text, system_prompt)
            results[submission['name']] = report
            
        progress_bar.progress((i + 1) / total_files)
        
        # 🚨 FIX 2: Pause for 2 seconds so Groq doesn't ban us for grading too fast
        if i < total_files - 1:
            await asyncio.sleep(2)
            
    status_text.text("Grading Complete!")
    return results

# ==========================================
# 6. STREAMLIT UI LAYOUT
# ==========================================
st.set_page_config(page_title="AI Bulk Grader", page_icon="📝", layout="wide")

st.title("📝 Automated B.Tech Code Grader")
st.markdown("Upload submissions to instantly generate structured JSON evaluations and **Downloadable PDF Reports**.")

persona_path = Path("commands/grader.md")
system_prompt_default = persona_path.read_text() if persona_path.exists() else "You are an AI Grader..."

rubric_path = Path("memory/rubric.md")
rubric_default = rubric_path.read_text() if rubric_path.exists() else "1. Code must run."

if "rubric_editor" not in st.session_state:
    st.session_state.rubric_editor = rubric_default

# --- INTERACTIVE SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configuration Editor")
    
    st.subheader("1. AI Rubric Generator")
    assignment_q = st.text_area("Plain English Question:", height=100)
    
    if st.button("✨ Auto-Generate Rubric"):
        if assignment_q:
            with st.spinner("Generating rigorous rubric..."):
                generated_text = asyncio.run(auto_generate_rubric(assignment_q))
                st.session_state.rubric_editor = generated_text
                st.rerun() 
        else:
            st.warning("Please enter a question first.")
    
    st.subheader("2. Review & Edit Rubric")
    final_rubric = st.text_area(
        "Final Grading Constraints:", 
        height=300,
        key="rubric_editor" 
    )
    
    with st.expander("Advanced: Edit AI Persona"):
        system_prompt = st.text_area("Grader Instructions:", value=system_prompt_default, height=300)

# --- MAIN UI AREA ---
# UPDATE: Ensure the uploader accepts ZIP files in the UI
# --- MAIN UI AREA ---
uploaded_files = st.file_uploader(
    "Upload Student Submissions (.zip, .py, .cpp, .c, .java, .js)", 
    accept_multiple_files=True,
    type=["zip", "py", "cpp", "c", "java", "js"]
)

if uploaded_files:
    if st.button("🚀 Start Bulk Grading", type="primary"):
        with st.spinner("Initializing AI Engine & Generating PDFs..."):
            reports = asyncio.run(process_files(uploaded_files, st.session_state.rubric_editor, system_prompt))            
            
            if reports:
                st.success("All files processed successfully!")
                
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                    for filename, report in reports.items():
                        if "error" in report:
                            st.error(f"Failed to grade {filename}: {report['error']}")
                            continue

                        with st.expander(f"📄 Report: {filename}"):
                            st.json(report)

                        pdf_bytes = create_pdf(filename, report)
                        pdf_filename = f"{filename.split('.')[0]}_GradingReport.pdf"
                        zip_file.writestr(pdf_filename, pdf_bytes)
                
                st.download_button(
                    label="📥 Download All PDF Reports (ZIP)",
                    data=zip_buffer.getvalue(),
                    file_name="Student_Grading_Reports_PDFs.zip",
                    mime="application/zip",
                    type="primary"
                )
# ==========================================
# 4. ZIP EXTRACTOR HELPER
# ==========================================
def extract_valid_files(uploaded_files):
    """Processes uploaded files, unzipping archives in memory and extracting source code."""
    extracted_submissions = []
    allowed_extensions = {".py", ".cpp", ".c", ".java", ".js"}

    for file in uploaded_files:
        if file.name.lower().endswith('.zip'):
            # Open the ZIP file in memory
            with zipfile.ZipFile(file, 'r') as z:
                for file_info in z.infolist():
                    # Skip directories and macOS hidden junk folders
                    if file_info.is_dir() or '__MACOSX' in file_info.filename:
                        continue
                    
                    ext = os.path.splitext(file_info.filename)[1].lower()
                    if ext in allowed_extensions:
                        # Read the file content
                        raw_bytes = z.read(file_info.filename)
                        content = raw_bytes.decode('utf-8', errors='replace')
                        
                        # Create a clear filename: "ZIPNAME_FILENAME"
                        student_id = file.name.replace('.zip', '')
                        clean_filename = f"{student_id}_{os.path.basename(file_info.filename)}"
                        
                        extracted_submissions.append({
                            "name": clean_filename,
                            "content": content
                        })
        else:
            # It's a standard loose file, process normally
            ext = os.path.splitext(file.name)[1].lower()
            if ext in allowed_extensions:
                extracted_submissions.append({
                    "name": file.name,
                    "content": file.getvalue().decode('utf-8', errors='replace')
                })
                
    return extracted_submissions