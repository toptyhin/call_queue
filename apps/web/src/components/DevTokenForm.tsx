import { useState, type FormEvent } from 'react'
import {
  getDevSession,
  logoutDevToken,
  mintDevToken,
  type DevSession,
} from '../lib/api'

type Props = {
  session: DevSession | null
  sessionLoading: boolean
  onSessionChange: (session: DevSession | null) => void
  onError: (message: string | null) => void
}

export function DevTokenForm({
  session,
  sessionLoading,
  onSessionChange,
  onError,
}: Props) {
  const [sub, setSub] = useState('dev-user')
  const [orgId, setOrgId] = useState('00000000-0000-4000-8000-000000000001')
  const [busy, setBusy] = useState(false)

  async function handleMintToken(e: FormEvent) {
    e.preventDefault()
    onError(null)
    setBusy(true)
    try {
      await mintDevToken({
        sub,
        org_id: orgId,
        role: 'authenticated',
      })
      const next = await getDevSession()
      onSessionChange(next)
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleLogout() {
    onError(null)
    setBusy(true)
    try {
      await logoutDevToken()
      onSessionChange({
        authenticated: false,
        sub: null,
        org_id: null,
        role: null,
      })
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const authenticated = Boolean(session?.authenticated)

  return (
    <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Dev-токен
      </h2>
      <form onSubmit={handleMintToken} className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block text-slate-600">sub</span>
          <input
            className="w-full rounded border border-slate-300 px-2 py-1.5"
            value={sub}
            onChange={(e) => setSub(e.target.value)}
            required
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-slate-600">org_id</span>
          <input
            className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
            value={orgId}
            onChange={(e) => setOrgId(e.target.value)}
            required
          />
        </label>
        <div className="sm:col-span-2 flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={busy || sessionLoading}
            className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white hover:bg-slate-700 disabled:opacity-40"
          >
            Выпустить токен
          </button>
          {authenticated ? (
            <button
              type="button"
              onClick={() => void handleLogout()}
              disabled={busy}
              className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-40"
            >
              Выйти
            </button>
          ) : null}
          <span className="text-xs text-slate-500">
            роль: authenticated · HttpOnly cookie `dev_token`
          </span>
        </div>
      </form>
      {sessionLoading ? (
        <p className="text-sm text-slate-500">Проверка сессии…</p>
      ) : authenticated ? (
        <p className="rounded bg-emerald-50 p-2 text-sm text-emerald-900">
          Cookie установлен
          {session?.sub ? (
            <span className="ml-2 font-mono text-xs text-emerald-800">
              sub={session.sub}
              {session.org_id ? ` · org=${session.org_id}` : ''}
            </span>
          ) : null}
        </p>
      ) : (
        <p className="text-sm text-slate-500">Cookie сессии ещё нет</p>
      )}
    </section>
  )
}
