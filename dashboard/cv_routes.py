"""CV management routes: upload, parse, edit, preview."""

import os
from pathlib import Path

import yaml
from flask import Blueprint, render_template, request, redirect, url_for, send_file, flash

from core.db import log_audit, get_conn
from core.logger import get_logger

log = get_logger(__name__)
cv_bp = Blueprint("cv", __name__)

RESUMES_DIR = Path("data/resumes")
RESUME_PATH = Path("config/resume.yaml")
TEMPLATE_PATH = Path("config/resume_template.yaml")
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def _trigger_parse(file_path: Path) -> list[str]:
    """Run the CV parse pipeline and return missing fields.

    Args:
        file_path: Path to the uploaded resume file.

    Returns:
        List of missing required field paths.
    """
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        from agents.cv_manager.parser_pdf import extract_text
    elif ext == ".docx":
        from agents.cv_manager.parser_docx import extract_text
    else:
        from agents.cv_manager.parser_txt import extract_text

    raw_text = extract_text(file_path)
    if not raw_text:
        return ["Failed to extract text from file"]

    from agents.cv_manager.structurer import structure_resume, save_resume
    from agents.cv_manager.validator import validate
    from agents.cv_manager.diff_detector import store_version

    data = structure_resume(raw_text)
    if not data:
        return ["LLM failed to structure resume"]

    missing = validate(data)
    save_resume(data)
    yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True)
    store_version(file_path, yaml_str, missing, source="upload")
    log_audit("cv_manager", "upload_parsed", f"file={file_path.name}")
    return missing


@cv_bp.route("/")
def cv_home():
    """Display the current parsed resume."""
    resume = {}
    if RESUME_PATH.exists():
        resume = yaml.safe_load(RESUME_PATH.read_text(encoding="utf-8")) or {}
    return render_template("cv_preview.html", resume=resume)


@cv_bp.route("/upload", methods=["GET", "POST"])
def upload():
    """Handle CV file upload and trigger parsing."""
    if request.method == "GET":
        return render_template("cv_upload.html")

    file = request.files.get("resume")
    if not file or not file.filename:
        flash("No file selected", "error")
        return redirect(url_for("cv.upload"))

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        flash(f"Unsupported file type: {suffix}. Use PDF, DOCX, or TXT.", "error")
        return redirect(url_for("cv.upload"))

    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    dest = RESUMES_DIR / file.filename
    file.save(dest)

    missing = _trigger_parse(dest)
    if missing:
        return redirect(url_for("cv.missing_fields"))
    flash("Resume parsed and saved successfully.", "success")
    return redirect(url_for("cv.cv_home"))


@cv_bp.route("/edit", methods=["GET", "POST"])
def edit():
    """Full form editor for resume fields."""
    if request.method == "GET":
        resume = {}
        if RESUME_PATH.exists():
            resume = yaml.safe_load(RESUME_PATH.read_text(encoding="utf-8")) or {}
        return render_template("cv_editor.html", resume=resume)

    form_yaml = request.form.get("resume_yaml", "")
    try:
        data = yaml.safe_load(form_yaml)
        if not isinstance(data, dict):
            flash("Invalid YAML content", "error")
            return redirect(url_for("cv.edit"))
        RESUME_PATH.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        log_audit("cv_manager", "manual_edit_saved", "via dashboard editor")
        return redirect(url_for("cv.edit") + "?saved=1")
    except yaml.YAMLError as e:
        flash(f"YAML error: {e}", "error")
    return redirect(url_for("cv.edit"))


@cv_bp.route("/missing", methods=["GET", "POST"])
def missing_fields():
    """Show and collect missing required resume fields."""
    if request.method == "POST":
        conn = get_conn()
        for key, value in request.form.items():
            if value.strip():
                conn.execute(
                    "INSERT INTO user_inputs (field_path, value) VALUES (?,?)",
                    (key, value.strip())
                )
        conn.commit()
        flash("Missing fields saved. Please update your resume via the editor.", "success")
        return redirect(url_for("cv.cv_home"))

    resume = {}
    if RESUME_PATH.exists():
        resume = yaml.safe_load(RESUME_PATH.read_text(encoding="utf-8")) or {}
    from agents.cv_manager.validator import validate
    missing = validate(resume)
    return render_template("cv_missing.html", missing=missing)


@cv_bp.route("/download")
def download_resume():
    """Download the original uploaded resume PDF."""
    resumes_dir = RESUMES_DIR.resolve()
    # Find most recently modified PDF, fallback to any PDF
    pdfs = sorted(resumes_dir.glob("*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not pdfs:
        flash("No resume PDF found. Please upload your resume first.", "error")
        return redirect(url_for("cv.cv_home"))
    return send_file(str(pdfs[0]), as_attachment=True, download_name=pdfs[0].name)


@cv_bp.route("/sample")
def sample():
    """Download the resume template YAML."""
    return send_file(str(TEMPLATE_PATH.resolve()), as_attachment=True, download_name="resume_template.yaml")


@cv_bp.route("/preview")
def preview():
    """Read-only view of the current parsed resume."""
    resume = {}
    if RESUME_PATH.exists():
        resume = yaml.safe_load(RESUME_PATH.read_text(encoding="utf-8")) or {}
    return render_template("cv_preview.html", resume=resume)
