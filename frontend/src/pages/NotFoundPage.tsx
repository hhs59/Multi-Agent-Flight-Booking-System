import { SearchX } from 'lucide-react'
import { Button, EmptyState } from '../components/ui'

export function NotFoundPage() {
  return (
    <div className="page">
      <EmptyState
        icon={<SearchX size={24} />}
        title="That page has moved"
        description="We could not find this page."
        action={<Button to="/assistant">Back to assistant</Button>}
      />
    </div>
  )
}
