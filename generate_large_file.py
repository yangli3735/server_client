# generate_large_file.py
import string
import random

def generate_large_file(filename, size_kb):
    # 生成约 size_kb KB 的随机数据
    with open(filename, 'w') as f:
        # 写入 50 行重复的文本作为基础内容
        base_text = "ALICE'S ADVENTURES IN WONDERLAND. Testing TCP Sliding Window and SACK logic.\n"
        f.write(base_text * 100) 
        
        # 补充随机字符达到目标大小
        chars = string.ascii_letters + string.digits + " "
        remaining_chars = (size_kb * 1024) - f.tell()
        if remaining_chars > 0:
            f.write(''.join(random.choice(chars) for _ in range(remaining_chars)))

if __name__ == "__main__":
    generate_large_file('large_test.txt', 800) # 生成 800KB 文件
    print("File 'large_test.txt' generated (800KB).")