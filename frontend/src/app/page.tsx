"use client"

import { ArrowUpRight01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import Image from "next/image"
import { useRouter } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import { AppFooter } from "@/components/app-footer"
import { AppHeader } from "@/components/app-header"
import { FileUploader } from "@/components/file-uploader"
import { GitHubUrlInput } from "@/components/github-url-input"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useAuth } from "@/hooks/use-auth"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useSessionStorage } from "@/hooks/use-session-storage"
import { API_BASE } from "@/lib/api"
import { startJob, startJobFromGitHub } from "@/lib/jobs"
import { addRecentJob, removeRecentJob, type RecentJob } from "@/lib/recent-jobs"
import { inferPackageName } from "@/lib/upload-utils"
import { createZipFromFiles } from "@/lib/zip"
import { useUploadStore } from "@/store/upload-store"
import openaiSmall from "../../public/openai-small.svg"
import paradigmSmall from "../../public/paradigm-small.svg"

const GITHUB_URL_PATTERN =
  /^https?:\/\/github\.com\/[^/]+\/[^/]+(\/tree\/[^/]+(\/.*)?)?$/

export default function Page() {
  const router = useRouter()
  const {
    mode,
    files,
    packageName,
    githubUrl,
    setMode,
    setUpload,
    setGitHubUrl,
    setFromGitHub,
    clearUpload,
  } = useUploadStore()
  const [openaiKey, setOpenaiKey] = useSessionStorage("evmbench.openaiKey", "")
  const [model, setModel] = useState("claude-opus-4-5")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [recentJobs, setRecentJobs] = useLocalStorage<RecentJob[]>(
    "evmbench.recentJobs.v1",
    [],
  )
  const {
    isAuthorized,
    isLoading: isAuthLoading,
    isConfigLoading,
    keyPredefined,
  } = useAuth()

  const fileCount = files?.length ?? 0
  const selectedLabel = useMemo(() => {
    if (mode === "github" && githubUrl.trim()) {
      // Extract repo name from GitHub URL
      const match = githubUrl.match(/github\.com\/[^/]+\/([^/]+)/)
      return match ? match[1].replace(/\.git$/, "") : "github-repo"
    }
    if (packageName) return packageName
    if (files) return inferPackageName(files)
    return null
  }, [mode, githubUrl, files, packageName])

  const isValidGitHubUrl = useMemo(() => {
    return GITHUB_URL_PATTERN.test(githubUrl.trim())
  }, [githubUrl])

  const canSubmit = useMemo(() => {
    if (isSubmitting || isAuthLoading || !isAuthorized) return false
    if (mode === "files") {
      return !!files && fileCount > 0
    }
    return isValidGitHubUrl
  }, [mode, files, fileCount, isValidGitHubUrl, isSubmitting, isAuthLoading, isAuthorized])

  const handleFilesSelected = useCallback(
    (selected: File[]) => {
      setUpload(selected, inferPackageName(selected))
    },
    [setUpload],
  )

  const handleKeyChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setOpenaiKey(event.target.value)
    },
    [setOpenaiKey],
  )

  const handleSubmit = async () => {
    if (!isAuthorized) {
      setSubmitError("Authorize with GitHub to start analysis.")
      return
    }

    if (mode === "files" && (!files || fileCount === 0)) return
    if (mode === "github" && !isValidGitHubUrl) return

    const trimmedKey = openaiKey.trim()

    setIsSubmitting(true)
    setSubmitError(null)

    try {
      const name = selectedLabel ?? "files"
      let response

      if (mode === "github") {
        response = await startJobFromGitHub(githubUrl.trim(), model, trimmedKey)
        setFromGitHub(true)
      } else {
        const zipFile = await createZipFromFiles(files!, name)
        response = await startJob(zipFile, model, trimmedKey)
        setFromGitHub(false)
      }

      // Persist locally so users can navigate back without server-side auth/history.
      const next = addRecentJob({
        job_id: response.job_id,
        label: name,
        created_at_ms: Date.now(),
        source: mode,
      })
      setRecentJobs(next)
      router.push(`/results?job_id=${response.job_id}`)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Upload failed")
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <main className="flex min-h-screen w-screen flex-col">
      <AppHeader showLogo={false} showBorder={false} />
      <section className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-4xl">
          <div className="mx-auto grid max-w-sm gap-10 lg:max-w-none lg:grid-cols-5">
            <div className="space-y-6 lg:col-span-3">
              <div>
                <div className="-ms-2 mb-3 flex items-center gap-2">
                  <a
                    href="https://openai.com"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Image
                      src={openaiSmall}
                      alt="OpenAI"
                      className="size-12 dark:invert"
                    />
                  </a>
                  <div className="h-9 w-px bg-border" />
                  <a
                    href="https://paradigm.xyz"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Image
                      src={paradigmSmall}
                      alt="Paradigm"
                      className="size-12 dark:invert"
                    />
                  </a>
                </div>
                <h1 className="text-5xl leading-[1.1] font-serif text-foreground mb-1.5">
                  evmbench
                </h1>
                <h2 className="text-2xl leading-[1.1] font-serif text-foreground mb-3">
                  Evaluating AI performance on high-severity contract findings
                </h2>
                <div className="space-y-2 text-base text-foreground/80">
                  <p className="leading-tight">
                    evmbench is an open benchmark from OpenAI and Paradigm that
                    evaluates whether AI agents can detect, patch, and exploit
                    high-severity vulnerabilities.
                  </p>
                  <p className="leading-tight">
                    This interface focuses on detection and only reports
                    high-severity findings. Upload a contract folder, provide an
                    API key, and start a run.
                  </p>
                  <div className="flex flex-col items-start gap-0.5">
                    <a
                      href="https://www.paradigm.xyz/2026/02/evmbench"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      Read the blog post
                      <HugeiconsIcon
                        icon={ArrowUpRight01Icon}
                        strokeWidth={2}
                        className="size-3.5"
                      />
                    </a>
                    <a
                      href="https://github.com/paradigmxyz/evmbench"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      View the repo
                      <HugeiconsIcon
                        icon={ArrowUpRight01Icon}
                        strokeWidth={2}
                        className="size-3.5"
                      />
                    </a>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-6 lg:col-span-2">
              <Tabs
                value={mode}
                onValueChange={(v) => setMode(v as "files" | "github")}
              >
                <TabsList className="grid w-full grid-cols-2">
                  <TabsTrigger value="files">Upload Files</TabsTrigger>
                  <TabsTrigger value="github">GitHub URL</TabsTrigger>
                </TabsList>
                <TabsContent value="files" className="mt-4">
                  <FileUploader
                    onFilesSelected={handleFilesSelected}
                    files={files}
                    selectedLabel={selectedLabel}
                    fileCount={fileCount}
                    disabled={isSubmitting}
                    onClear={clearUpload}
                  />
                </TabsContent>
                <TabsContent value="github" className="mt-4">
                  <GitHubUrlInput
                    value={githubUrl}
                    onChange={setGitHubUrl}
                    disabled={isSubmitting}
                  />
                </TabsContent>
              </Tabs>

              <div className="grid gap-3 text-xs text-muted-foreground">
                {!isConfigLoading && !keyPredefined && (
                  <div className="grid gap-1">
                    <Label
                      htmlFor="openai-key"
                      className="text-xs text-foreground"
                    >
                      OpenAI API Key
                    </Label>
                    <Input
                      id="openai-key"
                      type="password"
                      placeholder="sk-&hellip;"
                      value={openaiKey}
                      onChange={handleKeyChange}
                    />
                  </div>
                )}
                <div className="grid gap-1">
                  <Label
                    htmlFor="model-select"
                    className="text-xs text-foreground"
                  >
                    Model
                  </Label>
                  <Select value={model} onValueChange={setModel}>
                    <SelectTrigger id="model-select" className="w-full">
                      <SelectValue placeholder="Select model" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="claude-opus-4-5">Claude Opus 4.5</SelectItem>
                      <SelectItem value="gpt-5.2">GPT-5.2</SelectItem>
                      <SelectItem value="gemini-3-flash-preview">Gemini 3 Flash</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {!isAuthLoading && !isAuthorized && (
                  <span className="text-base font-serif text-muted-foreground">
                    <a
                      href={`${API_BASE}/v1/auth/`}
                      className="text-foreground underline underline-offset-2 hover:text-primary"
                    >
                      Authorize
                    </a>{" "}
                    to start analysis.
                  </span>
                )}
                <Button
                  onClick={handleSubmit}
                  disabled={!canSubmit}
                  className="w-full uppercase"
                >
                  {isSubmitting ? "Uploading…" : "Start analysis"}
                </Button>
                {submitError && (
                  <div className="text-xs text-destructive">{submitError}</div>
                )}

                {recentJobs.length > 0 && (
                  <div className="pt-1">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-xs text-muted-foreground">
                        Recent runs
                      </span>
                      <button
                        type="button"
                        onClick={() => setRecentJobs([])}
                        className="text-xs text-muted-foreground hover:text-foreground"
                      >
                        Clear
                      </button>
                    </div>
                    <div className="mt-2 space-y-1">
                      {recentJobs.slice(0, 6).map((job) => (
                        <div
                          key={job.job_id}
                          className="group flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/40"
                        >
                          <button
                            type="button"
                            onClick={() =>
                              router.push(`/results?job_id=${job.job_id}`)
                            }
                            className="flex min-w-0 flex-1 items-center justify-between gap-3"
                            title={job.job_id}
                          >
                            <span className="min-w-0 flex-1 truncate text-foreground">
                              {job.label}
                            </span>
                            <span className="shrink-0 font-mono text-muted-foreground">
                              {job.job_id.slice(0, 8)}
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              setRecentJobs(removeRecentJob(job.job_id))
                            }}
                            className="shrink-0 p-0.5 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100"
                            title="Remove from list"
                          >
                            <svg
                              xmlns="http://www.w3.org/2000/svg"
                              width="14"
                              height="14"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <path d="M18 6 6 18" />
                              <path d="m6 6 12 12" />
                            </svg>
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>
      <AppFooter showBorder={false} />
    </main>
  )
}
