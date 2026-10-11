"""TEMPORARY probe: which of several causes swallows piped answers in a .bat prompt sequence."""
import os
import subprocess
import tempfile

PS = 'powershell -NoProfile -Command "\'x\'"'
VARIANTS = {
    "no-powershell": "rem",
    "powershell": f'for /f "usebackq delims=" %%S in (`{PS}`) do set "R=%%S"',
    "powershell ^<nul": f'for /f "usebackq delims=" %%S in (`{PS} ^<nul`) do set "R=%%S"',
    "cmd /c <nul wrapper": f'for /f "usebackq delims=" %%S in (`cmd /c "{PS} <nul"`) do set "R=%%S"',
    "powershell -InputFormat None": 'for /f "usebackq delims=" %%S in (`powershell -NoProfile -InputFormat None -Command "\'x\'"`) do set "R=%%S"',
    "plain powershell stmt": PS.replace("powershell", "powershell") + " >nul",
}
for name, middle in VARIANTS.items():
    for label, data in (("CRLF", b"one\r\ntwo\r\nthree\r\n"), ("LF", b"one\ntwo\nthree\n")):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            bat = os.path.join(d, "p.bat")
            with open(bat, "w", newline="\r\n") as f:
                f.write('@echo off\nsetlocal enabledelayedexpansion\nset "A="\nset "B="\nset "C="\nset "R="\n'
                        'set /p A="a? "\n' + middle + '\nset /p B="b? "\nset /p C="c? "\n'
                        'echo.\necho RESULT A=[!A!] B=[!B!] C=[!C!]\n')
            ans = os.path.join(d, "answers.txt")
            with open(ans, "wb") as f:
                f.write(data)
            with open(ans, "rb") as fin:
                out = subprocess.run(["cmd", "/d", "/c", bat], stdin=fin, capture_output=True, timeout=60).stdout
            res = [l.strip() for l in out.decode("utf-8", "replace").replace("\r", "\n").splitlines()
                   if l.startswith("RESULT")]
            print(f"{name:32} {label:5} {res}", flush=True)
