import { useEffect } from 'react'

export function useTheme() {
  const theme = 'dark'

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [])

  const setTheme = () => {}
  const toggleTheme = () => {}

  return { theme, setTheme, toggleTheme }
}
