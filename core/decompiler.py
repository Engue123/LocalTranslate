import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import io
from pathlib import Path
from typing import Tuple, List

# URL for downloading unrpyc repository zip
UNRPYC_ZIP_URL = "https://github.com/CensoredUsername/unrpyc/archive/refs/heads/master.zip"

def get_unrpyc_path() -> Path:
    """Returns the path to the unrpyc.py script, downloading it if not present."""
    base_dir = Path(__file__).parent.parent
    vendor_dir = base_dir / "vendor"
    unrpyc_script = vendor_dir / "unrpyc-master" / "unrpyc.py"
    
    if not unrpyc_script.exists():
        vendor_dir.mkdir(parents=True, exist_ok=True)
        print("unrpyc decompiler not found. Downloading from GitHub...")
        try:
            # Using urllib with a user-agent to avoid potential HTTP block
            req = urllib.request.Request(
                UNRPYC_ZIP_URL,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                zip_data = response.read()
            
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zip_ref:
                zip_ref.extractall(vendor_dir)
            
            if unrpyc_script.exists():
                print("unrpyc downloaded and extracted successfully.")
            else:
                raise FileNotFoundError("Could not find unrpyc.py after extracting zip.")
        except Exception as e:
            raise RuntimeError(f"Failed to download or extract unrpyc: {e}")
            
    return unrpyc_script

def decompile_if_needed(game_dir: Path, temp_base_dir: Path) -> Tuple[Path, List[str], List[Path]]:
    """
    Checks if game_dir contains .rpy files. If not, but contains .rpyc,
    downloads unrpyc and decompiles .rpyc files into a temporary directory.
    Returns:
        - Path to scan for scripts (original game_dir or temporary decompiled folder)
        - List of warning/error messages
        - List of temporary paths created (for cleanup)
    """
    def has_rpy_files(directory: Path) -> bool:
        for root, dirs, files in os.walk(directory):
            if "tl" in dirs:
                dirs.remove("tl")
            for file in files:
                if file.endswith(".rpy"):
                    return True
        return False

    def get_rpyc_files(directory: Path) -> List[Path]:
        rpyc_list = []
        for root, dirs, files in os.walk(directory):
            if "tl" in dirs:
                dirs.remove("tl")
            for file in files:
                if file.endswith(".rpyc"):
                    rpyc_list.append(Path(root) / file)
        return rpyc_list

    # 1. If .rpy files exist, no decompilation is needed
    if has_rpy_files(game_dir):
        return game_dir, [], []

    # 2. Check for .rpyc files
    rpyc_files = get_rpyc_files(game_dir)
    if not rpyc_files:
        return game_dir, ["No Ren'Py scripts (.rpy or .rpyc) found in the game directory."], []

    # Only .rpyc files are present. Decompilation is required.
    warnings = []
    temp_dir = temp_base_dir / "decompiled_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_paths = [temp_dir]
    
    try:
        unrpyc_path = get_unrpyc_path()
    except Exception as e:
        warnings.append(f"Decompilation failed: {e}. Machine translation will be skipped.")
        return game_dir, warnings, []

    print(f"Only .rpyc files detected. Decompiling {len(rpyc_files)} files...")
    
    for rpyc_file in rpyc_files:
        try:
            rel_path = rpyc_file.relative_to(game_dir)
        except ValueError:
            rel_path = Path(rpyc_file.name)
            
        output_rpy = temp_dir / rel_path.with_suffix(".rpy")
        output_rpy.parent.mkdir(parents=True, exist_ok=True)
        
        cmd = [sys.executable, str(unrpyc_path), str(rpyc_file), "-o", str(output_rpy)]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            if not output_rpy.exists():
                warnings.append(f"Decompilation succeeded but output file not found: {rel_path}")
        except subprocess.CalledProcessError as e:
            warnings.append(f"Failed to decompile {rel_path}: {e.stderr.strip()}")
        except Exception as e:
            warnings.append(f"Unexpected error decompiling {rel_path}: {e}")

    if not has_rpy_files(temp_dir):
        warnings.append("No files were successfully decompiled. Cannot parse game scripts.")
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        return game_dir, warnings, []

    return temp_dir, warnings, temp_paths

def cleanup_temp_paths(paths: List[Path]) -> None:
    """Removes temporary directories and files generated during decompilation."""
    for path in paths:
        if path.exists():
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except Exception as e:
                print(f"Warning: Failed to clean up temporary path {path}: {e}")
