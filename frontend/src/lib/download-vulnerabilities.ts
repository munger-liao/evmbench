import type { Vulnerability } from "@/types"

function vulnerabilityToMarkdown(vuln: Vulnerability): string {
  const lines: string[] = []

  lines.push(`## ${vuln.id}: ${vuln.title}`)
  lines.push("")
  lines.push(`**Severity:** ${vuln.severity.toUpperCase()}`)
  lines.push("")

  if (vuln.summary) {
    lines.push("### Summary")
    lines.push(vuln.summary)
    lines.push("")
  }

  if (vuln.impact) {
    lines.push("### Impact")
    lines.push(vuln.impact)
    lines.push("")
  }

  if (vuln.description && vuln.description.length > 0) {
    lines.push("### Description")
    for (const loc of vuln.description) {
      lines.push("")
      lines.push(`**File:** \`${loc.file}\` (lines ${loc.line_start}-${loc.line_end})`)
      lines.push("")
      lines.push(loc.desc)
    }
    lines.push("")
  }

  if (vuln.proof_of_concept) {
    lines.push("### Proof of Concept")
    lines.push(vuln.proof_of_concept)
    lines.push("")
  }

  if (vuln.remediation) {
    lines.push("### Remediation")
    lines.push(vuln.remediation)
    lines.push("")
  }

  return lines.join("\n")
}

export function generateVulnerabilityMarkdown(vuln: Vulnerability): string {
  return `# Vulnerability Report\n\n${vulnerabilityToMarkdown(vuln)}`
}

export function generateAllVulnerabilitiesMarkdown(
  vulnerabilities: Vulnerability[],
  jobId?: string
): string {
  const lines: string[] = []

  lines.push("# Security Audit Report")
  lines.push("")
  if (jobId) {
    lines.push(`**Job ID:** ${jobId}`)
    lines.push("")
  }
  lines.push(`**Total Vulnerabilities:** ${vulnerabilities.length}`)
  lines.push("")

  // Summary by severity
  const bySeverity = vulnerabilities.reduce(
    (acc, v) => {
      acc[v.severity] = (acc[v.severity] || 0) + 1
      return acc
    },
    {} as Record<string, number>
  )

  lines.push("### Summary by Severity")
  lines.push("")
  for (const [severity, count] of Object.entries(bySeverity)) {
    lines.push(`- **${severity.toUpperCase()}:** ${count}`)
  }
  lines.push("")
  lines.push("---")
  lines.push("")

  // Individual vulnerabilities
  for (const vuln of vulnerabilities) {
    lines.push(vulnerabilityToMarkdown(vuln))
    lines.push("---")
    lines.push("")
  }

  return lines.join("\n")
}

export function downloadMarkdown(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export function downloadVulnerability(vuln: Vulnerability): void {
  const content = generateVulnerabilityMarkdown(vuln)
  const filename = `${vuln.id}-${vuln.title.slice(0, 30).replace(/[^a-zA-Z0-9]/g, "-")}.md`
  downloadMarkdown(content, filename)
}

export function downloadAllVulnerabilities(
  vulnerabilities: Vulnerability[],
  jobId?: string
): void {
  const content = generateAllVulnerabilitiesMarkdown(vulnerabilities, jobId)
  const filename = jobId
    ? `audit-report-${jobId.slice(0, 8)}.md`
    : "audit-report.md"
  downloadMarkdown(content, filename)
}
