#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filepack.py - 把整个文件夹打包成分块 txt，并可一键还原。

用法:
  打包:  python filepack.py pack <源文件夹> [输出前缀] [--chunk-mb 20]
         生成 <前缀>.part001.txt, <前缀>.part002.txt, ...
         (只有一个块时直接生成 <前缀>.txt)
  还原:  python filepack.py unpack <任意一个块文件 或 前缀> <目标文件夹>

说明:
  - 编码用 base85 (比 base85 之外的 base64 少 ~8% 字符，二进制无损)。
  - 每个文件记录 SHA256，还原时逐文件校验。
  - 打包默认跳过 .git、node_modules、__pycache__、.venv 目录。
"""
import base64
import hashlib
import os
import re
import sys

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".idea", ".vscode"}
BEGIN = "===== FILE BEGIN ====="
END = "===== FILE END ====="
CHUNK_HEADER = "##### filepack v2 base85"
DEFAULT_CHUNK_MB = 20


class ChunkWriter:
    """按大小切分写入多个文件: <prefix>.part001.txt ..."""

    def __init__(self, prefix: str, chunk_bytes: int):
        self.prefix = prefix
        self.chunk_bytes = chunk_bytes
        self.index = 0
        self.written = 0
        self.total = 0
        self.fh = None

    def _open_next(self):
        self._close()
        self.index += 1
        if self.index == 1 and self.total == 0 and self.chunk_bytes <= 0:
            # 单文件模式（chunk<=0 表示不分块）
            path = f"{self.prefix}.txt"
        else:
            path = f"{self.prefix}.part{self.index:03d}.txt"
        self.fh = open(path, "w", encoding="utf-8", newline="\n")
        self.written = 0
        if self.index == 1:
            self.fh.write(CHUNK_HEADER + "\n")

    def write(self, text: str):
        if self.fh is None:
            self._open_next()
        n = len(text.encode("utf-8"))
        if (self.chunk_bytes > 0 and self.written > 0
                and self.written + n > self.chunk_bytes):
            self._open_next()
        self.fh.write(text)
        self.written += n
        self.total += n

    def _close(self):
        if self.fh:
            self.fh.close()

    def finish(self):
        self._close()


def find_chunks(path: str):
    """给定任意一个块文件或前缀，返回排序后的全部块路径。"""
    if os.path.isfile(path):
        # 单文件包，或用户给了某一块 -> 用 part 序号推断同组块
        m = re.match(r"^(.*)\.part(\d+)\.txt$", path)
        if not m:
            return [path]
        prefix, num = m.group(1), int(m.group(2))
        if num != 1:
            first = f"{prefix}.part001.txt"
            if os.path.isfile(first):
                path = first
    # path 现在是首块 (或单文件)
    m = re.match(r"^(.*)\.part001\.txt$", path)
    if m:
        prefix = m.group(1)
        chunks = []
        i = 1
        while os.path.isfile(f"{prefix}.part{i:03d}.txt"):
            chunks.append(f"{prefix}.part{i:03d}.txt")
            i += 1
        return chunks
    return [path]


class MultiReader:
    """把多个块文件当单个流顺序读取。"""

    def __init__(self, paths):
        self.paths = paths
        self.i = 0
        self.fh = open(paths[0], "r", encoding="utf-8")

    def readline(self) -> str:
        line = self.fh.readline()
        if line:
            return line
        self.fh.close()
        self.i += 1
        if self.i >= len(self.paths):
            return ""
        self.fh = open(self.paths[self.i], "r", encoding="utf-8")
        return self.fh.readline()


def pack(src_dir: str, out_prefix: str, chunk_mb: float) -> None:
    src_dir = os.path.abspath(src_dir)
    if not os.path.isdir(src_dir):
        sys.exit(f"错误: 源文件夹不存在: {src_dir}")
    chunk_bytes = int(chunk_mb * 1024 * 1024) if chunk_mb > 0 else 0
    writer = ChunkWriter(out_prefix, chunk_bytes)
    count = 0
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, src_dir).replace(os.sep, "/")
            with open(path, "rb") as f:
                data = f.read()
            sha = hashlib.sha256(data).hexdigest()
            b85 = base64.b85encode(data).decode("ascii")
            writer.write(f"{BEGIN} path: {rel} sha256: {sha}\n{b85}\n{END}\n")
            count += 1
    writer.finish()
    size = writer.total
    print(f"完成: 打包 {count} 个文件, {writer.index} 个块, "
          f"共 {size/1024/1024:.2f} MB -> {out_prefix}.part001.txt 等" if writer.index > 1
          else f"完成: 打包 {count} 个文件 -> {out_prefix}.txt ({size/1024/1024:.2f} MB)")


def unpack(pack_path: str, dest_dir: str) -> None:
    chunks = find_chunks(pack_path)
    if not chunks:
        sys.exit(f"错误: 找不到打包文件: {pack_path}")
    reader = MultiReader(chunks)
    count = 0
    while True:
        line = reader.readline()
        if not line:
            break
        line = line.rstrip("\n")
        if not line.startswith(BEGIN):
            continue
        m = re.match(r"^path: (.*) sha256: ([0-9a-f]{64})$", line[len(BEGIN):].strip())
        if not m:
            sys.exit(f"错误: 无法解析文件头: {line}")
        rel = m.group(1).replace("/", os.sep)
        b85 = reader.readline().rstrip("\n")
        end = reader.readline().rstrip("\n")
        if end != END:
            sys.exit(f"错误: 文件 {rel} 的记录不完整 (缺少 END 标记)")
        data = base64.b85decode(b85)
        if hashlib.sha256(data).hexdigest() != m.group(2):
            sys.exit(f"错误: 文件 {rel} 校验失败")
        target = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(target) or dest_dir, exist_ok=True)
        with open(target, "wb") as out:
            out.write(data)
        count += 1
    print(f"完成: 还原 {count} 个文件 (来自 {len(chunks)} 个块) -> {os.path.abspath(dest_dir)}")


def main() -> None:
    args = sys.argv[1:]
    chunk_mb = DEFAULT_CHUNK_MB
    if "--chunk-mb" in args:
        i = args.index("--chunk-mb")
        chunk_mb = float(args[i + 1])
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = args[0].lower()
    if cmd == "pack":
        src = args[1]
        prefix = args[2] if len(args) > 2 else os.path.basename(os.path.abspath(src))
        pack(src, prefix, chunk_mb)
    elif cmd == "unpack":
        if len(args) < 3:
            print(__doc__)
            sys.exit(1)
        unpack(args[1], args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
