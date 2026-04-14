const BASE = ""  // same origin — nginx proxies /api/* to pimiproxy-api

async function req(method, path, body) {
  const opts = {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(`${BASE}/api/${path}`, opts)
  if (res.status === 401) throw Object.assign(new Error("Unauthorized"), { status: 401 })
  const text = await res.text()
  const data = text ? JSON.parse(text) : {}
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

export const api = {
  login:         (password)      => req("POST",   "login",          { password }),
  logout:        ()              => req("POST",   "logout"),
  status:        ()              => req("GET",    "status"),
  config:        ()              => req("GET",    "config"),
  addGroup:      (group)         => req("POST",   "groups",          group),
  deleteGroup:   (name)          => req("DELETE", `groups/${encodeURIComponent(name)}`),
  addRule:       (rule)          => req("POST",   "rules",           rule),
  deleteRule:    (index)         => req("DELETE", `rules/${index}`),
  reorderRules:  (order)         => req("PUT",    "rules/reorder",   { order }),
  setProvider:        (url)      => req("POST",   "provider",          { url }),
  refresh:            ()         => req("POST",   "refresh"),
  setRefreshSettings: (settings) => req("POST",   "refresh-settings",  settings),
}
