"use client"

import { Github01Icon, Cancel01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import { useCallback, useMemo } from "react"
import { Input } from "@/components/ui/input"

interface GitHubUrlInputProps {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
}

const GITHUB_URL_PATTERN =
  /^https?:\/\/github\.com\/[^/]+\/[^/]+(\/tree\/[^/]+(\/.*)?)?$/

export function GitHubUrlInput({
  value,
  onChange,
  disabled,
}: GitHubUrlInputProps) {
  const handleChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      onChange(event.target.value)
    },
    [onChange],
  )

  const handleClear = useCallback(() => {
    onChange("")
  }, [onChange])

  const isValid = useMemo(() => {
    if (!value.trim()) return null
    return GITHUB_URL_PATTERN.test(value.trim())
  }, [value])

  const hasValue = value.trim().length > 0

  return (
    <div className="space-y-1.5">
      <div className="relative">
        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
          <HugeiconsIcon
            icon={Github01Icon}
            className="size-4 text-muted-foreground"
          />
        </div>
        <Input
          type="url"
          placeholder="https://github.com/owner/repo"
          value={value}
          onChange={handleChange}
          disabled={disabled}
          className="pl-9 pr-9"
        />
        {hasValue && (
          <button
            type="button"
            onClick={handleClear}
            disabled={disabled}
            className="absolute inset-y-0 right-0 flex items-center pr-3 text-muted-foreground hover:text-foreground disabled:pointer-events-none"
          >
            <HugeiconsIcon icon={Cancel01Icon} className="size-4" />
          </button>
        )}
      </div>
      {isValid === false && (
        <p className="text-xs text-destructive">
          Invalid GitHub URL. Use format: https://github.com/owner/repo
        </p>
      )}
      {isValid === true && (
        <p className="text-xs text-muted-foreground">
          Repository will be downloaded and analyzed
        </p>
      )}
    </div>
  )
}
