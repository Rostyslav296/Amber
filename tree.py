# save as treegen.py
import os
from pathlib import Path

def write_tree(root, out_file, prefix=""):
    entries = sorted(os.listdir(root))
    for i, entry in enumerate(entries):
        path = Path(root) / entry
        connector = "└── " if i == len(entries) - 1 else "├── "
        out_file.write(prefix + connector + entry + "\n")
        if path.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            write_tree(path, out_file, prefix + extension)

if __name__ == "__main__":
    folder = input("Enter folder path: ").strip('"')
    outfile = Path("folder_tree.txt")
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(folder + "\n")
        write_tree(folder, f)
    print(f"Tree written to {outfile.resolve()}")
