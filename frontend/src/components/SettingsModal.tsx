import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '../lib/api'
import type { Health, Provider } from '../types'
import './settings.css'

type RuntimeValues = Pick<Health, 'max_steps' | 'idle_timeout' | 'playwright_mcp_enabled' | 'file_mcp_enabled'>

export function SettingsModal({ health, onSaved, onClose }: {
  health: Health | null
  onSaved: (values: RuntimeValues) => void
  onClose: () => void
}) {
  const [maxSteps, setMaxSteps] = useState(String(health?.max_steps ?? 50))
  const [idleMinutes, setIdleMinutes] = useState(String((health?.idle_timeout ?? 3600) / 60))
  const [playwrightEnabled, setPlaywrightEnabled] = useState(health?.playwright_mcp_enabled ?? true)
  const [fileEnabled, setFileEnabled] = useState(health?.file_mcp_enabled ?? true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (health) {
      setMaxSteps(String(health.max_steps))
      setIdleMinutes(String(health.idle_timeout / 60))
      setPlaywrightEnabled(health.playwright_mcp_enabled)
      setFileEnabled(health.file_mcp_enabled)
    }
  }, [health])

  const steps = Number(maxSteps)
  const minutes = Number(idleMinutes)
  const valid = maxSteps !== '' && idleMinutes !== '' &&
    Number.isInteger(steps) && steps >= 1 && steps <= 500 &&
    Number.isInteger(minutes) && minutes >= 1 && minutes <= 1440
  const changed = steps !== health?.max_steps || minutes * 60 !== health?.idle_timeout || playwrightEnabled !== health?.playwright_mcp_enabled || fileEnabled !== health?.file_mcp_enabled

  async function save() {
    if (!valid || !changed || saving) return
    setSaving(true)
    setError('')
    try {
      const values = await api<RuntimeValues>('/api/settings', 'PATCH', {
        max_steps: steps,
        idle_timeout: minutes * 60,
        playwright_mcp_enabled: playwrightEnabled,
        file_mcp_enabled: fileEnabled,
      })
      onSaved(values)
    } catch (cause) {
      setError(String(cause))
    } finally {
      setSaving(false)
    }
  }

  return <div className="modal-backdrop" onClick={onClose}>
    <section className="settings-modal" role="dialog" aria-modal="true" aria-label="Settings" onClick={event => event.stopPropagation()}>
      <button className="modal-close icon-button" title="Close settings" onClick={onClose}><X size={19}/></button>
      <span className="eyebrow">DEV WORKSPACE</span>
      <h2>Make Orbit yours.</h2>
      <p className="settings-intro">Set credentials and model names in <code>browser-agent/.env</code>, then restart the backend. Keys are never sent to this interface.</p>
      <div className="settings-section-title">AI PROVIDERS</div>
      <div className="settings-providers">
        {(['openai', 'azure', 'anthropic'] as Provider[]).map(provider => <div className="provider-row" key={provider}>
          <div><strong>{provider === 'anthropic' ? 'Anthropic Claude' : provider === 'azure' ? 'Azure OpenAI' : 'OpenAI'}</strong><small>{health?.models[provider] || 'Model not configured'}</small></div>
          <span className={health?.providers[provider] ? 'ready' : 'not-ready'}>{health?.providers[provider] ? 'Configured' : 'Needs API key'}</span>
        </div>)}
      </div>
      <div className="settings-section-title settings-controls-title">AGENT & BROWSER</div>
      <div className="settings-controls">
        <label className="settings-control-row"><span>Maximum steps</span><input aria-label="Maximum steps" type="number" min="1" max="500" step="1" value={maxSteps} onChange={event => setMaxSteps(event.target.value)} /></label>
        <label className="settings-control-row"><span>Idle browser timeout</span><span className="settings-input-unit"><input aria-label="Idle browser timeout in minutes" type="number" min="1" max="1440" step="1" value={idleMinutes} onChange={event => setIdleMinutes(event.target.value)} /><span>min</span></span></label>
      </div>
      <div className="settings-section-title settings-mcp-title">MCP INTEGRATIONS</div>
      <div className="settings-mcp-list">
        {[
          { name: 'Playwright MCP', description: `Browser automation · ${health?.mcp === 'available' ? 'Installed' : 'Not installed'}`, enabled: playwrightEnabled, available: health?.mcp === 'available', toggle: () => setPlaywrightEnabled(enabled => !enabled) },
          { name: 'File MCP', description: 'Workspace files and Downloads · Built in', enabled: fileEnabled, available: true, toggle: () => setFileEnabled(enabled => !enabled) },
        ].map(integration => <div className="settings-mcp-row" key={integration.name}>
          <div className="settings-mcp-detail"><strong>{integration.name}</strong><span>{integration.description}</span></div>
          <span className="settings-mcp-state">{integration.enabled ? 'Enabled' : 'Disabled'}</span>
          <button type="button" className="settings-switch" role="switch" aria-label={`Enable ${integration.name}`} aria-checked={integration.enabled} disabled={!integration.available} onClick={integration.toggle}><span /></button>
        </div>)}
      </div>
      <p className="settings-mcp-note">Save changes to apply. Disabling Playwright MCP closes active browsers. Disabling File MCP blocks file tools, workspace notes, and the Files panel.</p>
      {error && <div className="settings-error" role="alert">{error}</div>}
      <p className="settings-fine">The app and history stay on this computer. Prompts and page observations are sent to your selected AI provider. Enter passwords directly in the browser, never in chat.</p>
      <div className="settings-actions">
        <button className="settings-done" onClick={onClose}>Back to workspace</button>
        <button className="primary settings-save" disabled={!health || !valid || !changed || saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save changes'}</button>
      </div>
    </section>
  </div>
}
