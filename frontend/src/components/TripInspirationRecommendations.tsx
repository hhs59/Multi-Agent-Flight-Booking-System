import { Compass } from 'lucide-react'
import { formatMoney } from '../lib/format'
import { isRecord } from '../types/api'
import { Button } from './ui'

type Money = { amount: string; currency: string }

type BudgetComparison = {
  approximateFare: Money
}

type InspirationOption = {
  rank: number
  city: string
  countryCode: string
  amount: string
  currency: string
  reason: string
  budgetComparison: BudgetComparison | null
}

type InspirationResult = {
  status: string
  budget: Money | null
  options: InspirationOption[]
}

export function isTripInspirationResult(value: unknown): boolean {
  return isRecord(value) && value.action === 'trip_inspiration' && typeof value.status === 'string'
}

function parseMoney(value: unknown): Money | null {
  return isRecord(value) && typeof value.amount === 'string' && typeof value.currency === 'string'
    ? { amount: value.amount, currency: value.currency }
    : null
}

function parseInspiration(value: Record<string, unknown>): InspirationResult {
  const constraints = isRecord(value.constraints) ? value.constraints : null
  const recommendations = Array.isArray(value.recommendations) ? value.recommendations : []
  const options = recommendations.flatMap((item): InspirationOption[] => {
    if (!isRecord(item)) return []
    const fare = parseMoney(item.lowest_verified_fare)
    const comparison = isRecord(item.budget_comparison) ? item.budget_comparison : null
    const approximateFare = comparison ? parseMoney(comparison.approximate_fare) : null
    if (
      typeof item.rank !== 'number' ||
      typeof item.city !== 'string' ||
      typeof item.country_code !== 'string' ||
      !fare ||
      typeof item.reason !== 'string'
    ) {
      return []
    }
    return [
      {
        rank: item.rank,
        city: item.city,
        countryCode: item.country_code,
        amount: fare.amount,
        currency: fare.currency,
        reason: item.reason,
        budgetComparison: approximateFare ? { approximateFare } : null,
      },
    ]
  })
  return {
    status: typeof value.status === 'string' ? value.status : '',
    budget: constraints ? parseMoney(constraints.airfare_budget) : null,
    options,
  }
}

export function TripInspirationRecommendations({
  value,
  onSelectOption,
}: {
  value: unknown
  onSelectOption?: (rank: number, city: string) => void
}) {
  if (!isTripInspirationResult(value) || !isRecord(value)) return null
  const result = parseInspiration(value)
  if (result.status !== 'results' || !result.options.length) return null

  return (
    <section className="trip-inspiration-result" aria-label="Airfare matches">
      <div className="inspiration-heading">
        <Compass size={17} />
        <div>
          <h3>Airfare matches</h3>
          <p>{summary(result.budget)}</p>
        </div>
      </div>
      <ul className="inspiration-list">
        {result.options.map((option) => (
          <li key={option.rank + '-' + option.city}>
            <div className="inspiration-destination">
              <strong>
                {option.city}, {option.countryCode}
              </strong>
              <span>{option.reason}</span>
            </div>
            <div className="inspiration-price">
              <strong>{formatMoney(option.amount, option.currency)}</strong>
              {option.budgetComparison ? (
                <span>
                  About{' '}
                  {formatMoney(
                    option.budgetComparison.approximateFare.amount,
                    option.budgetComparison.approximateFare.currency,
                  )}
                </span>
              ) : null}
            </div>
            <Button
              variant="secondary"
              size="sm"
              aria-label={`View flights for ${option.city}`}
              onClick={() => onSelectOption?.(option.rank, option.city)}
            >
              View flights
            </Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function summary(budget: Money | null): string {
  return budget
    ? `One current fare per destination within your ${formatMoney(budget.amount, budget.currency)} airfare budget.`
    : 'One current fare per destination. Select one to see all available flights.'
}
