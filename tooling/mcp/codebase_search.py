from fastmcp import FastMCP
import ast
import os
from pathlib import Path
import concurrent.futures

mcp = FastMCP("codebase-search")

# Resolve the repo root from this file's own location (tooling/mcp/codebase_search.py)
# so searches scan the whole bot regardless of the process's working directory.
REPO_ROOT = Path(__file__).resolve().parents[2]

def get_python_files(directory=None):
    base = Path(directory) if directory is not None else REPO_ROOT
    files = []
    for path in base.rglob("*.py"):
        rel = path.relative_to(base)
        if any(part.startswith(".") for part in rel.parts) or "__pycache__" in rel.parts or "Models" in rel.parts:
            continue
        files.append(path)
    return files

def execute_with_timeout(func, *args, timeout_sec=5):
    """Executes a function with a timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args)
        try:
            return future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            return None
        except Exception:
            return None

@mcp.tool()
def outline(filepath: str) -> str:
    """Returns an outline of the classes and functions in the given Python file."""
    def _process():
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
        
        result = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                result.append(f"Class: {node.name} (Line {node.lineno})")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        result.append(f"  Method: {item.name} (Line {item.lineno})")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result.append(f"Function: {node.name} (Line {node.lineno})")
        return result

    try:
        result = execute_with_timeout(_process)
        if result is None:
            return f"Error: Processing {filepath} timed out or failed."
        return "\n".join(result) if result else "No classes or functions found."
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def where_is(name: str) -> str:
    """Finds the definition (class or function) of the given name in the codebase."""
    results = []
    
    def _process(filepath):
        local_res = []
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(filepath))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                type_str = "Class" if isinstance(node, ast.ClassDef) else "Function"
                local_res.append(f"Found {type_str} '{name}' in {filepath} at line {node.lineno}")
        return local_res

    for filepath in get_python_files():
        res = execute_with_timeout(_process, filepath)
        if res:
            results.extend(res)
            
    return "\n".join(results) if results else f"'{name}' not found."

class ReferenceVisitor(ast.NodeVisitor):
    def __init__(self, name, filepath):
        self.name = name
        self.filepath = filepath
        self.results = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == self.name:
            self.results.append(f"Call to '{self.name}' in {self.filepath} at line {node.lineno}")
            for arg in node.args:
                self.visit(arg)
            for kw in node.keywords:
                self.visit(kw)
        elif isinstance(node.func, ast.Attribute) and node.func.attr == self.name:
            self.results.append(f"Method call to '{self.name}' in {self.filepath} at line {node.lineno}")
            self.visit(node.func.value)
            for arg in node.args:
                self.visit(arg)
            for kw in node.keywords:
                self.visit(kw)
        else:
            self.generic_visit(node)

    def visit_Name(self, node):
        if node.id == self.name and isinstance(node.ctx, ast.Load):
            self.results.append(f"Reference to '{self.name}' in {self.filepath} at line {node.lineno}")
        self.generic_visit(node)

@mcp.tool()
def find_references(name: str) -> str:
    """Finds references (calls or uses) of the given name in the codebase."""
    results = []
    
    def _process(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(filepath))
        visitor = ReferenceVisitor(name, str(filepath))
        visitor.visit(tree)
        return visitor.results

    for filepath in get_python_files():
        res = execute_with_timeout(_process, filepath)
        if res:
            results.extend(res)
            
    return "\n".join(results) if results else f"No references to '{name}' found."

if __name__ == "__main__":
    mcp.run()
