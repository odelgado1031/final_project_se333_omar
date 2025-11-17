# server.py
import os
import json
import shutil
import subprocess
import ast
import operator as op
from pathlib import Path
from typing import Optional, Dict, Any, List
import xml.etree.ElementTree as ET

# ---- robust import shim for the MCP decorator -------------------------------
# Tries several fastmcp layouts (tool vs tools; package vs module).
tool = None
try:
    from fastmcp import tool as _t  # some versions export here
    if callable(_t):
        tool = _t
except Exception:
    pass
if not callable(tool):
    try:
        # module with function 'tool'
        from fastmcp.tools.tool import tool as _t
        if callable(_t):
            tool = _t
    except Exception:
        pass
if not callable(tool):
    try:
        # module with function 'tools'
        from fastmcp.tools.tool import tools as _t
        if callable(_t):
            tool = _t
    except Exception:
        pass
if not callable(tool):
    try:
        # sometimes importing the module then digging inside works
        import fastmcp.tools.tool as tmod
        tool = getattr(tmod, "tool", None) or getattr(tmod, "tools", None)
    except Exception:
        pass
if not callable(tool):
    try:
        # package-level 'tools' fallback
        from fastmcp.tools import tools as _t
        if callable(_t):
            tool = _t
    except Exception:
        pass
if not callable(tool):
    # Final fallback: identity decorator so local Python usage still works.
    # (If you use an MCP client later, install/upgrade fastmcp so a real decorator exists.)
    def tool(*dargs, **dkwargs):
        def decorator(fn):
            return fn
        return decorator

# ---- paths ------------------------------------------------------------------
THIS_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
POM_PATH = os.path.join(PROJECT_ROOT, "codebase", "pom.xml")

# ---- tiny sanity helper ------------------------------------------------------
def echo(text: str) -> str:
    return text

# ---- process runner ----------------------------------------------------------
def _run(cmd: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=cwd or PROJECT_ROOT,
        text=True,
        capture_output=True,
        shell=False,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    return {
        "command": " ".join(cmd),
        "cwd": os.path.abspath(cwd or PROJECT_ROOT),
        "returncode": proc.returncode,
        "stdout_tail": out[-2000:],
        "stderr_tail": err[-2000:],
    }

# ---- Maven wrappers ----------------------------------------------------------
def run_maven(test_filter: Optional[str] = None, goals: str = "test") -> Dict[str, Any]:
    mvn_path = shutil.which("mvn") or "mvn"
    cmd = ["cmd", "/c", mvn_path, "-f", POM_PATH]
    if test_filter:
        cmd.append(f"-Dtest={test_filter}")
    cmd.append(goals)
    return _run(cmd, cwd=PROJECT_ROOT)

def maven_test(test_filter: Optional[str] = None) -> Dict[str, Any]:
    return run_maven(test_filter=test_filter, goals="test")

# ---- safe little calculator (exposed as an MCP tool) ------------------------
_ALLOWED = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.USub: op.neg,
}

def _eval(node):
    if isinstance(node, ast.Num):
        return node.n
    if isinstance(node, ast.UnaryOp):
        return _ALLOWED[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp):
        return _ALLOWED[type(node.op)](_eval(node.left), _eval(node.right))
    raise ValueError("unsupported expression")

@tool()
def calc(expression: str) -> float:
    """Evaluate an arithmetic expression, e.g. '1+2*3'."""
    tree = ast.parse(expression, mode="eval")
    return float(_eval(tree.body))

@tool()
def echo_tool(text: str) -> str:
    """Echo text back to the caller."""
    return echo(text)

@tool()
def maven_test_tool(test_filter: str = "") -> Dict[str, Any]:
    """Run Maven tests with an optional -Dtest filter and return the tail output."""
    return maven_test(test_filter or None)

@tool()
def maven_test_and_report() -> dict:
    """
    Run tests and produce a JaCoCo XML report, returning a simple coverage summary.
    """
    repo = Path(PROJECT_ROOT)
    codebase = repo / "codebase"
    cmd = ["mvn", "-f", str(codebase / "pom.xml"), "clean", "test", "jacoco:report"]
    out = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)

    report = codebase / "target" / "site" / "jacoco" / "jacoco.xml"
    summary: Dict[str, float] = {}
    if report.exists():
        tree = ET.parse(report)
        counters = {"LINE": {"covered": 0, "missed": 0}, "BRANCH": {"covered": 0, "missed": 0}}
        for c in tree.iterfind(".//counter"):
            t = c.attrib["type"]
            if t in counters:
                counters[t]["covered"] += int(c.attrib["covered"])
                counters[t]["missed"] += int(c.attrib["missed"])

        def pct(kind: str) -> float:
            cov = counters[kind]["covered"]
            miss = counters[kind]["missed"]
            return round(100.0 * cov / (cov + miss), 2) if cov + miss else 0.0

        summary = {"line_coverage_pct": pct("LINE"), "branch_coverage_pct": pct("BRANCH")}

    return {"returncode": out.returncode, "summary": summary}

@tool()
def uncovered_classes(threshold: float = 80.0) -> List[dict]:
    """
    List classes with line coverage below `threshold` percent.
    Requires that you've already run maven_test_and_report().
    """
    repo = Path(PROJECT_ROOT)
    report = repo / "codebase" / "target" / "site" / "jacoco" / "jacoco.xml"
    if not report.exists():
        raise RuntimeError("Run maven_test_and_report first.")
    tree = ET.parse(report)
    low: List[dict] = []
    for cls in tree.iterfind(".//class"):
        name = cls.attrib["name"].replace("/", ".")
        line_cov = 0.0
        for c in cls.iterfind(".//counter"):
            if c.attrib["type"] == "LINE":
                covered = int(c.attrib["covered"])
                missed = int(c.attrib["missed"])
                line_cov = round(100.0 * covered / (covered + missed), 2) if covered + missed else 0.0
                break
        if line_cov < threshold:
            low.append({"class": name, "line_coverage_pct": line_cov})
    return low

if __name__ == "__main__":
    print("server.py loaded")

# --- ASGI app for uvicorn ----------------------------------------------------
try:
    from fastapi import FastAPI
except ImportError:
    raise RuntimeError(
        "fastapi is required to run this server over HTTP. "
        "Install it in your venv:  pip install fastapi uvicorn"
    )

app = FastAPI(title="SE333 MCP Helper", version="0.1.0")

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post("/echo")
def api_echo(payload: dict):
    # expects {"text": "..."}
    return {"result": echo(payload.get("text", ""))}

@app.post("/maven/test")
def api_maven_test(payload: dict | None = None):
    # optional payload: {"test_filter": "org.example.MyTest#method"}
    test_filter = (payload or {}).get("test_filter")
    return maven_test(test_filter=test_filter)
