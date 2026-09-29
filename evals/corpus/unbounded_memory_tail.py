class LogTail:
    def __init__(self, path):
        self.path = path
        self.lines = []

    def follow(self):
        with open(self.path) as fh:
            for line in fh:
                self.lines.append(line)
                yield line
