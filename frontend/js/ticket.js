// grayroll-05 变更单号识别（与 backend/tickets.py 同口径的 UI 镜像）。
// 仅用于发布页实时提示；最终以后端写口校验为准。
const CHANGE_TICKET_RE = new RegExp(
  [
    "(?:变更单号|变更编号|变更单|单号|change[\\s_-]?ticket|ticket\\s*no\\.?|ticket|issue|jira)",
    "\\s*[:：#]?\\s*(?=[A-Za-z0-9._-]{1,40}\\d)",
    "([A-Za-z0-9][A-Za-z0-9._-]{1,40})",
    "|([A-Z]{2,12}[-_][A-Z0-9]*\\d{2,}(?:[-_]\\d{2,})*)",
    "|([A-Z]{2,12}\\d{3,})",
    "|(#\\d{3,})",
  ].join(""),
  "i"
);

export function extractChangeTicket(text) {
  const m = String(text ?? "").match(CHANGE_TICKET_RE);
  if (!m) return "";
  return m[1] || m[2] || m[3] || m[4] || m[0];
}

export function hasChangeTicket(text) {
  return Boolean(extractChangeTicket(text));
}
