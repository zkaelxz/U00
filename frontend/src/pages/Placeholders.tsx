import { routeHref } from '../router'

function Placeholder({ title }: { title: string }) {
  return (
    <section className="panel" aria-label={title}>
      <h2>{title}</h2>
      <p className="muted">Coming soon in the React app. It still lives in the Streamlit app.</p>
      <a href={routeHref({ name: 'library' })}>Back to Library</a>
    </section>
  )
}

export const DramaPage = ({ id, stage }: { id: number; stage: string }) => (
  <Placeholder title={`Drama ${id} · ${stage}`} />
)
