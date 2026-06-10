import os
from PIL import Image

def convert_png_to_ico(png_path, ico_output_path):
    print(f"Loading source PNG from: {png_path}")
    if not os.path.exists(png_path):
        print("Source PNG does not exist!")
        return False
        
    try:
        img = Image.open(png_path)
        
        # Define standard icon sizes for Windows
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        
        print(f"Saving multi-size ICO to: {ico_output_path}")
        img.save(ico_output_path, format='ICO', sizes=sizes)
        print("ICO file successfully created.")
        return True
    except Exception as e:
        print(f"Error during conversion: {e}")
        return False

if __name__ == "__main__":
    src_png = r"C:\Users\igerg\.gemini\antigravity\brain\6273c716-7681-49f7-be48-945984c90da1\microphone_icon_source_1781106291236.png"
    dest_ico = r"c:\laragon\www\Projects\dictate\icon.ico"
    dest_png = r"c:\laragon\www\Projects\dictate\icon.png"
    
    # Also copy the original PNG to project directory as icon.png
    if os.path.exists(src_png):
        try:
            img = Image.open(src_png)
            img.save(dest_png)
            print(f"Saved source PNG to project folder as: {dest_png}")
        except Exception as e:
            print(f"Failed to copy PNG: {e}")
            
    convert_png_to_ico(src_png, dest_ico)
