import { create } from "zustand"

type UploadMode = "files" | "github"

interface UploadState {
  mode: UploadMode
  files: File[] | null
  packageName: string | null
  githubUrl: string
  fromGitHub: boolean
  setMode: (mode: UploadMode) => void
  setUpload: (files: File[] | null, packageName: string | null) => void
  setGitHubUrl: (url: string) => void
  setFromGitHub: (fromGitHub: boolean) => void
  clearUpload: () => void
}

export const useUploadStore = create<UploadState>((set) => ({
  mode: "files",
  files: null,
  packageName: null,
  githubUrl: "",
  fromGitHub: false,
  setMode: (mode) => set({ mode }),
  setUpload: (files, packageName) => set({ files, packageName }),
  setGitHubUrl: (githubUrl) => set({ githubUrl }),
  setFromGitHub: (fromGitHub) => set({ fromGitHub }),
  clearUpload: () =>
    set({ files: null, packageName: null, githubUrl: "", fromGitHub: false }),
}))
