from markbook import similarity as sim

ORIGINAL = '''
def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True

def primes_upto(limit):
    found = []
    for candidate in range(limit + 1):
        if is_prime(candidate):
            found.append(candidate)
    return found

def main():
    values = primes_upto(100)
    print(len(values))
    for v in values:
        print(v)
'''

RENAMED = '''
# check primality
def check(x):
    if x < 2:
        return False
    for k in range(2, int(x ** 0.5) + 1):
        if x % k == 0:
            return False
    return True

# collect them
def collect(top):
    out = []
    for c in range(top + 1):
        if check(c):
            out.append(c)
    return out

def main():
    items = collect(100)
    print(len(items))
    for item in items:
        print(item)
'''

DIFFERENT = '''
import sys
class Stack:
    def __init__(self):
        self.items = []
    def push(self, x):
        self.items.append(x)
    def pop(self):
        if not self.items:
            raise IndexError("empty")
        return self.items.pop()
    def peek(self):
        return self.items[-1] if self.items else None
    def size(self):
        return len(self.items)

def balanced(text):
    s = Stack()
    pairs = {")": "(", "]": "[", "}": "{"}
    for ch in text:
        if ch in "([{":
            s.push(ch)
        elif ch in pairs:
            if s.size() == 0 or s.pop() != pairs[ch]:
                return False
    return s.size() == 0
'''


def corpus(**files):
    return {name: sim.fingerprints(sim.tokenize(text, ".py")) for name, text in files.items()}


def test_renamed_copy_is_detected():
    pairs = sim.compare({"a": corpus(**{"p.py": ORIGINAL}), "b": corpus(**{"q.py": RENAMED}),
                         "c": corpus(**{"s.py": DIFFERENT})}, min_fingerprints=5)
    assert [(p.a, p.b) for p in pairs] == [("a", "b")]
    assert pairs[0].score > 0.9
    assert pairs[0].files[0]["a_file"] == "p.py" and pairs[0].files[0]["b_file"] == "q.py"


def test_unrelated_programs_not_flagged():
    assert sim.compare({"a": corpus(**{"p.py": ORIGINAL}), "c": corpus(**{"s.py": DIFFERENT})},
                       min_fingerprints=5) == []


def test_starter_code_is_subtracted():
    # Two students who only kept the teacher's starter file must not be flagged.
    a, b = {"p.py": sim.fingerprints(sim.tokenize(ORIGINAL, ".py"))}, {"p.py": sim.fingerprints(sim.tokenize(ORIGINAL, ".py"))}
    assert sim.compare({"a": a, "b": b}, min_fingerprints=5)
    assert sim.compare({"a": a, "b": b}, min_fingerprints=5, starter=a) == []


def test_common_boilerplate_ignored_in_large_cohorts():
    boiler = corpus(**{"m.py": ORIGINAL})
    cohort = {f"s{i}": dict(boiler) for i in range(8)}
    assert sim.compare(cohort, min_fingerprints=5) == [], "code shared by most of the cohort is boilerplate"


def test_tiny_files_never_flag():
    tiny = {"a": corpus(**{"x.py": "print(1)\n"}), "b": corpus(**{"x.py": "print(1)\n"})}
    assert sim.compare(tiny) == []


def test_tokenizer_ignores_comments_strings_and_names():
    t1 = sim.tokenize('x = "hello"  # c\nfoo(x, 12)\n', ".py")
    t2 = sim.tokenize('yy = "bye"\nbar(yy, 99) # other\n', ".py")
    assert t1 == t2


def test_c_like_comments_and_include_not_confused():
    toks = sim.tokenize('#include <stdio.h>\nint main(){ /* x */ return 0; } // y\n', ".c")
    assert "return" in toks and "x" not in toks
