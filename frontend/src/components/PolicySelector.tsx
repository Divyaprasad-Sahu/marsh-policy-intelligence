import { Check, FileCheck2 } from 'lucide-react'
import type { PolicySummary } from '../types'

const policyColors = ['indigo', 'cyan', 'violet', 'emerald']

interface Props {
  policies: PolicySummary[]
  selected: string[]
  onToggle: (id: string) => void
}

export function PolicySelector({ policies, selected, onToggle }: Props) {
  return (
    <div className="policy-grid" aria-label="Select policy documents">
      {policies.map((policy, index) => {
        const active = selected.includes(policy.policy_id)
        return (
          <button
            className={`policy-card ${active ? 'selected' : ''}`}
            data-tone={policyColors[index % policyColors.length]}
            key={policy.policy_id}
            onClick={() => onToggle(policy.policy_id)}
            type="button"
            aria-pressed={active}
          >
            <span className="policy-icon"><FileCheck2 size={18} /></span>
            <span className="policy-copy">
              <strong>{policy.product_name}</strong>
              <small>{policy.insurer}</small>
            </span>
            <span className="policy-check">{active ? <Check size={14} /> : null}</span>
          </button>
        )
      })}
    </div>
  )
}
