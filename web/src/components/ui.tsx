import type { ReactNode } from 'react'

export function Err({ msg }: { msg: string | null }) { return msg ? <p role="alert" className="text-danger type-body-sm my-2">{msg}</p> : null }

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`block rounded-surface border border-border bg-card p-3 ${className}`}>{children}</div>
}

