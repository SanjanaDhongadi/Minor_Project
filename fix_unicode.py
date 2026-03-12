# Fix all unicode characters in both agent files

files = [
    'agents/monitoring_agent.py',
    'agents/recovery_agent.py',
]

replacements = [
    ('\u2192', '->'),   # → arrow
    ('\u2014', '-'),    # — em dash
    ('\u2013', '-'),    # – en dash
    ('\U0001f4c9', '[DOWN]'),  # 📉
    ('\U0001f4c8', '[UP]'),    # 📈
    ('\u2705', '[OK]'),        # ✅
    ('\u26a0', '[WARN]'),      # ⚠
    ('\u274c', '[ERROR]'),     # ❌
    ('\u231b', '[WAIT]'),      # ⌛
    ('\u2500', '-'),           # ─
    ('\u2514', '+'),           # └
]

for filepath in files:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content
        for old, new in replacements:
            content = content.replace(old, new)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        changes = sum(1 for old, new in replacements if old in original)
        print(f'Fixed {filepath} ({changes} replacements)')

    except FileNotFoundError:
        print(f'File not found: {filepath}')
    except Exception as e:
        print(f'Error fixing {filepath}: {e}')

print('Done! Run python main.py now.')