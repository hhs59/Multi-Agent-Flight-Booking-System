import { useEffect, useRef, useState } from 'react'

export function VietnamAirlines3DBackground() {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [offset, setOffset] = useState({ x: 0, y: 0 })

  useEffect(() => {
    // 1. Mouse Parallax Tracking
    let targetX = 0
    let targetY = 0
    let currentX = 0
    let currentY = 0
    let rafId: number

    const handleMouseMove = (e: MouseEvent) => {
      const { innerWidth, innerHeight } = window
      targetX = (e.clientX / innerWidth - 0.5) * 30
      targetY = (e.clientY / innerHeight - 0.5) * 20
    }

    window.addEventListener('mousemove', handleMouseMove, { passive: true })

    const updateParallax = () => {
      currentX += (targetX - currentX) * 0.05
      currentY += (targetY - currentY) * 0.05
      setOffset({ x: currentX, y: currentY })
      rafId = requestAnimationFrame(updateParallax)
    }
    rafId = requestAnimationFrame(updateParallax)

    // 2. Photorealistic Cloud Dust & Golden Sun Particle Canvas
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let particleRafId: number
    const resizeCanvas = () => {
      canvas.width = window.innerWidth
      canvas.height = window.innerHeight
    }
    resizeCanvas()
    window.addEventListener('resize', resizeCanvas)

    // Golden light dust & floating vapor particles
    const particleCount = 45
    const particles = Array.from({ length: particleCount }, () => ({
      x: Math.random() * window.innerWidth,
      y: Math.random() * window.innerHeight,
      radius: Math.random() * 2.5 + 0.8,
      speedX: -(Math.random() * 0.4 + 0.2),
      speedY: (Math.random() - 0.5) * 0.15,
      alpha: Math.random() * 0.5 + 0.2,
      pulse: Math.random() * Math.PI * 2,
    }))

    const renderParticles = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)

      particles.forEach((p) => {
        p.x += p.speedX
        p.y += p.speedY
        p.pulse += 0.02
        const currentAlpha = p.alpha + Math.sin(p.pulse) * 0.15

        if (p.x < -10) p.x = canvas.width + 10
        if (p.y < -10) p.y = canvas.height + 10
        if (p.y > canvas.height + 10) p.y = -10

        ctx.beginPath()
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(255, 235, 180, ${Math.max(0, currentAlpha)})`
        ctx.shadowBlur = 10
        ctx.shadowColor = 'rgba(251, 191, 36, 0.6)'
        ctx.fill()
      })

      particleRafId = requestAnimationFrame(renderParticles)
    }
    renderParticles()

    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('resize', resizeCanvas)
      cancelAnimationFrame(rafId)
      cancelAnimationFrame(particleRafId)
    }
  }, [])

  return (
    <div ref={containerRef} className="vna-living-sky-wrapper" aria-hidden="true">
      {/* 1. High-Resolution Real Vietnam Airlines Flight Background with Subtle 3D Drifting */}
      <div
        className="vna-sky-image-layer"
        style={{
          transform: `scale(1.08) translate3d(${offset.x * -0.6}px, ${offset.y * -0.6}px, 0)`,
        }}
      >
        <img
          src="/images/vietnam_airlines_sky_alt.jpg"
          alt="Vietnam Airlines Boeing 787 Dreamliner flying above clouds"
          className="vna-sky-img"
        />
      </div>

      {/* 2. Soft Cloud Mist Overlay Layer (Drifting softly in foreground) */}
      <div
        className="vna-cloud-mist-layer"
        style={{
          transform: `translate3d(${offset.x * 0.8}px, ${offset.y * 0.8}px, 0)`,
        }}
      />

      {/* 3. Golden Sun Rays & Shimmering Ambient Light Layer */}
      <div className="vna-sun-rays-glow" />

      {/* 4. Canvas for Golden Flight Dust Particles */}
      <canvas ref={canvasRef} className="vna-particles-canvas" />

      {/* 5. Vignette & Readability Gradient Overlay */}
      <div className="vna-vignette-overlay" />
    </div>
  )
}
