import re

with open(r'template_Finalized.html', encoding='utf-8') as f:
    html = f.read()

lines = html.split('\n')

script_start = next(i for i,l in enumerate(lines) if re.search(r'<script(?![^>]*src=)', l))
script_end   = next(i for i in range(len(lines)-1, 0, -1) if '</script>' in lines[i])
print(f'Script block: lines {script_start+1}-{script_end+1}')

script = '\n'.join(lines[script_start:script_end+1])

braces = parens = 0
in_str = False
str_char = ''
in_line_comment = False
in_block_comment = False
in_regex = False
REGEX_PREV = set('=([,;[!&|?:~^')

history = []
pos = 0
cur_line = script_start + 1
prev_non_ws = ''

while pos < len(script):
    c = script[pos]
    n = script[pos+1] if pos+1 < len(script) else ''

    if c == '\n':
        history.append((cur_line, braces, parens, lines[cur_line-1].strip()[:100]))
        cur_line += 1
        in_line_comment = False
        pos += 1
        continue

    if in_line_comment:
        pos += 1
        continue
    if in_block_comment:
        if c == '*' and n == '/':
            in_block_comment = False
            pos += 1
        pos += 1
        continue
    if in_regex:
        if c == '\\':
            pos += 2
            continue
        if c == '[':
            pos += 1
            while pos < len(script) and script[pos] != ']':
                if script[pos] == '\\': pos += 1
                pos += 1
        elif c == '/':
            in_regex = False
            pos += 1
            while pos < len(script) and script[pos].isalpha():
                pos += 1
            continue
        pos += 1
        continue
    if in_str:
        if c == '\\':
            pos += 2
            continue
        if c == str_char:
            in_str = False
        pos += 1
        continue

    if c == '/' and n == '/': in_line_comment = True; pos += 1; continue
    if c == '/' and n == '*': in_block_comment = True; pos += 1; continue
    if c == '/' and prev_non_ws in REGEX_PREV:
        in_regex = True; pos += 1; continue
    if c in ('"', "'", '`'):
        in_str = True; str_char = c; pos += 1
        prev_non_ws = c
        continue

    if c == '{': braces += 1
    elif c == '}': braces -= 1
    elif c == '(': parens += 1
    elif c == ')': parens -= 1

    if c not in (' ', '\t'):
        prev_non_ws = c
    pos += 1

print(f'Final balance: braces={braces}  parens={parens}')

# Find last line where balance was 0, and first permanently-nonzero line
min_b_after = {}
cur_min = 9999
for ln, b, p, txt in reversed(history):
    min_b_after[ln] = cur_min
    cur_min = min(cur_min, b)

last_zero = None
first_perm_nonzero = None
for ln, b, p, txt in history:
    if b == 0 and p == 0:
        last_zero = (ln, txt)
    if (b != 0 or p != 0) and first_perm_nonzero is None and min_b_after.get(ln, 0) != 0:
        first_perm_nonzero = (ln, b, p, txt)

if last_zero:
    print(f'\nLast perfectly-balanced line: L{last_zero[0]}  {last_zero[1][:80]}')
if first_perm_nonzero:
    print(f'First permanently-imbalanced line: L{first_perm_nonzero[0]} [B:{first_perm_nonzero[1]:+d} P:{first_perm_nonzero[2]:+d}]  {first_perm_nonzero[3][:80]}')
    target = first_perm_nonzero[0]
    print(f'\n=== Context around line {target} (+-15 lines) ===')
    for ln, b, p, txt in history:
        if target-15 <= ln <= target+15:
            marker = ' <<<' if ln == target else ''
            print(f'L{ln:4d} [B:{b:+d} P:{p:+d}]{marker}  {txt}'.encode('ascii', 'replace').decode())
else:
    print('\nScript is balanced - no permanent imbalance found!')
    print('\nLast 10 lines:')
    for ln, b, p, txt in history[-10:]:
        print(f'L{ln:4d} [B:{b:+d} P:{p:+d}]  {txt}'.encode('ascii', 'replace').decode())
