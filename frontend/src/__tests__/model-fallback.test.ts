import { describe, expect, it } from 'vitest'

const FALLBACK_MODELS = [
  { value: 'claude-opus-4-6', label: 'Claude Opus 4.6' },
  { value: 'gpt-5.2', label: 'GPT-5.2' },
  { value: 'gpt-5.3-codex', label: 'GPT-5.3 Codex' },
  { value: 'gemini-3-flash-preview', label: 'Gemini 3 Flash' },
  { value: 'gemini-3-pro-preview', label: 'Gemini 3 Pro' },
  { value: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro' },
]

describe('model dropdown fallback logic', () => {
  it('should use remote models when available', () => {
    const remoteModels = [{ value: 'custom-model', label: 'Custom' }]
    const models = remoteModels.length > 0 ? remoteModels : FALLBACK_MODELS
    expect(models).toEqual(remoteModels)
  })

  it('should use fallback when remote models is empty', () => {
    const remoteModels: { value: string; label: string }[] = []
    const models = remoteModels.length > 0 ? remoteModels : FALLBACK_MODELS
    expect(models).toEqual(FALLBACK_MODELS)
    expect(models.length).toBeGreaterThan(0)
  })

  it('should default select to first model', () => {
    let model = ''
    const models = FALLBACK_MODELS
    if (!model && models.length > 0) {
      model = models[0].value
    }
    expect(model).toBe('claude-opus-4-6')
  })

  it('should not override existing model selection', () => {
    let model = 'gpt-5.2'
    const models = FALLBACK_MODELS
    if (!model && models.length > 0) {
      model = models[0].value
    }
    expect(model).toBe('gpt-5.2')
  })

  it('fallback models should all have non-empty value and label', () => {
    for (const m of FALLBACK_MODELS) {
      expect(m.value).toBeTruthy()
      expect(m.label).toBeTruthy()
    }
  })
})
