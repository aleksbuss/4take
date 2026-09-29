function truncateWithEllipsis(text, maxLen) {
  if (text.length < maxLen) {
    return text;
  }
  return text.slice(0, maxLen - 3) + "...";
}

function lastNLines(text, n) {
  const lines = text.split("\n");
  return lines.slice(lines.length - n - 1).join("\n");
}
