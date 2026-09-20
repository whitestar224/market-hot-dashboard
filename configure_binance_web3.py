"""Local, masked credential entry. Never accepts credentials on the command line."""
import queue
import threading
import tkinter as tk
from binance_web3 import BinanceWeb3Client, credential_path, save_credentials


def main():
    root = tk.Tk()
    root.title("星云社 · 币安 Web3 接口配置")
    width, height = 640, 410
    root.geometry(f"{width}x{height}+{(root.winfo_screenwidth()-width)//2}+{(root.winfo_screenheight()-height)//2}")
    root.configure(bg="#eef3f6")
    root.resizable(False, False)
    frame = tk.Frame(root, bg="#eef3f6", padx=28, pady=22)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="配置币安 Web3 API", bg="#eef3f6", fg="#142a38",
             font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
    tk.Label(frame, text="仅填写 API 凭证，不要输入助记词或钱包私钥。", bg="#eef3f6", fg="#5c7180",
             font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(8, 16))
    entries = []
    for label in ("API Key", "Secret Key"):
        tk.Label(frame, text=label, bg="#eef3f6", font=("Microsoft YaHei UI", 11)).pack(anchor="w")
        entry = tk.Entry(frame, show="●", font=("Microsoft YaHei UI", 13), relief="solid", bd=1)
        entry.pack(fill="x", ipady=7, pady=(4, 12))
        entries.append(entry)
    status = tk.Label(frame, text="保存后按当前 Windows 账户加密，不会写进项目或聊天。", bg="#eef3f6",
                      fg="#5c7180", wraplength=570, justify="left", font=("Microsoft YaHei UI", 10))
    status.pack(anchor="w", pady=(4, 12))
    replies = queue.Queue()
    def check_saved():
        button.configure(state="disabled")
        check_button.configure(state="disabled")
        status.configure(text="正在检测已保存凭证，会自动校准请求时间（不会交易）…", fg="#075669")
        def check():
            try:
                replies.put(BinanceWeb3Client().check_connection()["message"])
            except Exception as exc:
                replies.put("已保存，但连接未通过：" + (str(exc) if isinstance(exc, ValueError) else "请稍后重试"))
        threading.Thread(target=check, daemon=True).start()
    def save():
        try:
            save_credentials(entries[0].get(), entries[1].get())
        except Exception:
            status.configure(text="保存失败：请检查两项凭证格式及当前 Windows 账户。", fg="#ad392d")
            return
        for entry in entries:
            entry.delete(0, "end")
        check_saved()
    actions = tk.Frame(frame, bg="#eef3f6")
    actions.pack(fill="x")
    button = tk.Button(actions, text="加密保存并检查连接", command=save, bg="#f7bd3c", fg="#142a38",
                       relief="flat", pady=9, font=("Microsoft YaHei UI", 12, "bold"))
    button.pack(side="left", fill="x", expand=True, padx=(0, 8))
    check_button = tk.Button(actions, text="检测已保存配置", command=check_saved, bg="#dbe9f0", fg="#142a38",
                             relief="flat", pady=9, font=("Microsoft YaHei UI", 12, "bold"))
    check_button.pack(side="left", fill="x", expand=True)
    try:
        if credential_path().exists():
            status.configure(text="凭证已加密保存，可直接检测连接，无需重新填写。")
            root.after(300, check_saved)
    except ValueError:
        pass
    def poll():
        try:
            status.configure(text=replies.get_nowait())
            button.configure(state="normal")
            check_button.configure(state="normal")
        except queue.Empty:
            pass
        root.after(150, poll)
    root.after(150, poll)
    root.mainloop()


if __name__ == "__main__":
    main()
