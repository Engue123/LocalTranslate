import pickle
import zlib
from pathlib import Path
from typing import Dict, Tuple, List, Optional

class RPAExtractor:
    """A python-only reader/extractor for Ren'Py RPA archives."""
    
    def __init__(self, archive_path: Path):
        self.archive_path = Path(archive_path)
        self.handle = None
        self.version = None
        self.key = 0
        self.index = {}

    def open(self):
        self.handle = open(self.archive_path, "rb")
        self._parse_header()

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _parse_header(self):
        self.handle.seek(0)
        line = self.handle.readline()
        try:
            header = line.decode('utf-8', errors='ignore').strip()
        except Exception:
            header = ""

        if header.startswith('RPA-3.2'):
            self.version = 3.2
            vals = header.split()
            if len(vals) < 4:
                raise ValueError(f"Malformed RPA-3.2 header in {self.archive_path.name}")
            offset = int(vals[1], 16)
            self.key = 0
            for subkey in vals[3:]:
                self.key ^= int(subkey, 16)
        elif header.startswith('RPA-3.0'):
            self.version = 3.0
            vals = header.split()
            if len(vals) < 3:
                raise ValueError(f"Malformed RPA-3.0 header in {self.archive_path.name}")
            offset = int(vals[1], 16)
            self.key = 0
            for subkey in vals[2:]:
                self.key ^= int(subkey, 16)
        elif header.startswith('RPA-2.0'):
            self.version = 2.0
            vals = header.split()
            if len(vals) < 2:
                raise ValueError(f"Malformed RPA-2.0 header in {self.archive_path.name}")
            offset = int(vals[1], 16)
            self.key = 0
        elif self.archive_path.suffix == '.rpi':
            self.version = 1.0
            offset = 0
            self.key = 0
        else:
            raise ValueError(f"Not a valid Ren'Py archive format in {self.archive_path.name}")

        self.handle.seek(offset)
        compressed_index = self.handle.read()
        try:
            decompressed = zlib.decompress(compressed_index)
            # Use latin1 to safely unpickle string/byte keys and values in Python 3
            raw_index = pickle.loads(decompressed, encoding='latin1')
        except Exception as e:
            raise ValueError(f"Failed to decompress or unpickle index: {e}")

        # Deobfuscate index
        self.index = {}
        if self.version in [3.0, 3.2]:
            for filename, info in raw_index.items():
                deobf_info = []
                for entry in info:
                    if len(entry) == 2:
                        deobf_info.append((entry[0] ^ self.key, entry[1] ^ self.key, b''))
                    else:
                        prefix = entry[2]
                        if isinstance(prefix, str):
                            prefix = prefix.encode('latin1')
                        deobf_info.append((entry[0] ^ self.key, entry[1] ^ self.key, prefix))
                self.index[filename] = deobf_info
        else:
            # version 2.0 or 1.0
            for filename, info in raw_index.items():
                deobf_info = []
                for entry in info:
                    if len(entry) == 2:
                        deobf_info.append((entry[0], entry[1], b''))
                    else:
                        prefix = entry[2]
                        if isinstance(prefix, str):
                            prefix = prefix.encode('latin1')
                        deobf_info.append((entry[0], entry[1], prefix))
                self.index[filename] = deobf_info

    def list_files(self) -> List[str]:
        return list(self.index.keys())

    def read_file(self, filename: str) -> bytes:
        if filename not in self.index:
            raise FileNotFoundError(f"File {filename} not found in archive")

        chunks = []
        for offset, length, prefix in self.index[filename]:
            self.handle.seek(offset)
            data = self.handle.read(length - len(prefix))
            chunks.append(prefix + data)
        return b''.join(chunks)

    def extract_file(self, filename: str, output_path: Path):
        data = self.read_file(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)
