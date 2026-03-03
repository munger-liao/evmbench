import { describe, expect, it } from 'vitest'
import type { FrontendConfig, ModelOption } from '@/lib/integration'

describe('FrontendConfig types', () => {
  it('should accept a valid FrontendConfig with models', () => {
    const config: FrontendConfig = {
      auth_enabled: false,
      key_predefined: true,
      models: [
        { value: 'gpt-5.2', label: 'GPT-5.2' },
        { value: 'claude-opus-4-6', label: 'Claude Opus 4.6' },
      ],
    }
    expect(config.models).toHaveLength(2)
    expect(config.models[0].value).toBe('gpt-5.2')
    expect(config.models[0].label).toBe('GPT-5.2')
  })

  it('should accept empty models array', () => {
    const config: FrontendConfig = {
      auth_enabled: false,
      key_predefined: false,
      models: [],
    }
    expect(config.models).toHaveLength(0)
  })

  it('ModelOption should have value and label', () => {
    const option: ModelOption = { value: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro' }
    expect(option.value).toBe('gemini-3.1-pro-preview')
    expect(option.label).toBe('Gemini 3.1 Pro')
  })
})
