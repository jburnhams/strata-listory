import type { ReactNode } from 'react'

export function Err({ msg }: { msg: string | null }) { return msg ? <p role="alert" className="text-danger type-body-sm my-2">{msg}</p> : null }

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`block rounded-surface border border-border bg-card p-3 ${className}`}>{children}</div>
}

export const btn = 'rounded-control bg-primary text-primary-foreground px-3 py-1.5 type-body-sm hover:opacity-90 disabled:opacity-50'
export const btnQuiet = 'rounded-control border border-border px-2 py-0.5 type-caption hover:border-ring disabled:opacity-50 disabled:cursor-not-allowed'
export const input = 'rounded-control border border-border bg-transparent px-2 py-1.5 type-body-sm'
