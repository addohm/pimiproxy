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

async function download(path, filename) {
  const res = await fetch(`${BASE}/api/${path}`, { credentials: 'include' })
  if (res.status === 401) throw Object.assign(new Error('Unauthorized'), { status: 401 })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
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
  check:              (url)      => req("POST",   "check",             { url }),
  exportUserConfig:   ()         => download("export/user-config",   "pimiproxy-user-config.yaml"),
  exportProviderYaml: ()         => download("export/provider-yaml", "pimiproxy-provider.yaml"),
  exportFinalConfig:  ()         => download("export/final-config",  "pimiproxy-config.yaml"),
}
