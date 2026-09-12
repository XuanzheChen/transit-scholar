import type { AnchorHTMLAttributes, ReactNode } from 'react'
import { Link } from '../app/router'
import type { ButtonVariant } from './Button'

export interface LinkButtonProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  children?: ReactNode
}

/** Anchor styled as a button; keeps an `href` for normal browser behaviour. */
export function LinkButton({
  to,
  variant = 'secondary',
  size = 'md',
  className,
  children,
  ...rest
}: LinkButtonProps) {
  const classes = ['btn', `btn--${variant}`, `btn--${size}`, className].filter(Boolean).join(' ')
  return (
    <Link to={to} className={classes} {...rest}>
      <span>{children}</span>
    </Link>
  )
}
