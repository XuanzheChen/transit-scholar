import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'

export interface FieldProps {
  label: string
  /** id of the control this label describes. */
  htmlFor?: string
  description?: ReactNode
  error?: ReactNode
  required?: boolean
  children: ReactNode
}

/** Labelled form row with optional help text and validation message. */
export function Field({ label, htmlFor, description, error, required, children }: FieldProps) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={htmlFor}>
        {label}
        {required ? <span className="field__required"> (required)</span> : null}
      </label>
      {children}
      {description ? <p className="field__description">{description}</p> : null}
      {error ? (
        <p className="field__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}

export type TextInputProps = InputHTMLAttributes<HTMLInputElement>

export function TextInput({ className, ...rest }: TextInputProps) {
  return <input {...rest} className={['input', className].filter(Boolean).join(' ')} />
}

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>

export function Select({ className, children, ...rest }: SelectProps) {
  return (
    <select {...rest} className={['select', className].filter(Boolean).join(' ')}>
      {children}
    </select>
  )
}

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>

export function Textarea({ className, ...rest }: TextareaProps) {
  return <textarea {...rest} className={['textarea', className].filter(Boolean).join(' ')} />
}

export interface RadioOption {
  value: string
  label: string
  description?: ReactNode
  disabled?: boolean
}

export interface RadioGroupProps {
  name: string
  legend: string
  value: string
  options: RadioOption[]
  onChange: (value: string) => void
}

/**
 * Radio group rendered as selectable cards.
 *
 * Used where a choice changes the meaning of the whole form (for example
 * whether a Workspace is created with or without a Schema binding).
 */
export function RadioGroup({ name, legend, value, options, onChange }: RadioGroupProps) {
  return (
    <fieldset className="radio-group">
      <legend className="radio-group__legend">{legend}</legend>
      {options.map((option) => {
        const optionId = `${name}-${option.value}`
        const selected = option.value === value
        return (
          <div
            className={`radio-card${selected ? ' radio-card--selected' : ''}${
              option.disabled ? ' radio-card--disabled' : ''
            }`}
            key={option.value}
          >
            <input
              className="radio-card__input"
              type="radio"
              id={optionId}
              name={name}
              value={option.value}
              checked={selected}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
              data-testid={`${name}-${option.value}`}
            />
            <label className="radio-card__body" htmlFor={optionId}>
              <span className="radio-card__label">{option.label}</span>
              {option.description ? (
                <span className="radio-card__description">{option.description}</span>
              ) : null}
            </label>
          </div>
        )
      })}
    </fieldset>
  )
}
