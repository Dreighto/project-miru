import os

ASSETS_ROOT = r"D:\Miru_Assets"

for folder in sorted(os.listdir(ASSETS_ROOT)):
    folder_path = os.path.join(ASSETS_ROOT, folder)
    if not os.path.isdir(folder_path):
        continue
    subfolders = sorted(os.listdir(folder_path))
    has_subs = False
    for sub in subfolders:
        sub_path = os.path.join(folder_path, sub)
        if os.path.isdir(sub_path):
            count = len([f for f in os.listdir(sub_path) if f.endswith(".png")])
            if count > 0:
                print(f"{folder}\\{sub}\\ — {count} files")
                has_subs = True
    if not has_subs:
        count = len([f for f in os.listdir(folder_path) if f.endswith(".png")])
        if count > 0:
            print(f"{folder}\\ — {count} files (no subfolders)")
