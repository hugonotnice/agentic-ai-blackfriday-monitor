@php
    use App\Enums\Trend;
    use function Filament\Support\get_color_css_variables;

    /** @var \App\Models\Product $product */
    /** @var \App\Dto\PriceCacheDto $latestPrice */
    $latestPrice = $product->getPriceCache()->first();

    $curVal = $latestPrice?->getUnitPrice() ?? (float) $product->current_price;
    $targetVal = (float) ($product->notify_price ?? 0);
    $distPct = ($targetVal > 0 && $curVal > 0) ? (($curVal - $targetVal) / $targetVal) * 100 : null;
    $metaFmt = $targetVal > 0 ? 'R$ ' . number_format($targetVal, 2, ',', '.') : null;

    $semaforo = null;
    if ($distPct !== null) {
        if ($distPct <= 0) {
            $semaforo = [
                'bg' => 'rgba(16, 185, 129, 0.16)',
                'border' => 'rgba(16, 185, 129, 0.45)',
                'color' => '#34d399',
                'dot' => '#10b981',
                'label' => $distPct <= -0.5
                    ? number_format(abs($distPct), 1, ',', '.') . '% abaixo · Comprar'
                    : 'Na meta · Comprar',
            ];
        } elseif ($distPct <= 15) {
            $semaforo = [
                'bg' => 'rgba(245, 158, 11, 0.16)',
                'border' => 'rgba(245, 158, 11, 0.45)',
                'color' => '#fbbf24',
                'dot' => '#f59e0b',
                'label' => '+' . number_format($distPct, 1, ',', '.') . '% da meta',
            ];
        } else {
            $semaforo = [
                'bg' => 'rgba(239, 68, 68, 0.16)',
                'border' => 'rgba(239, 68, 68, 0.45)',
                'color' => '#f87171',
                'dot' => '#ef4444',
                'label' => '+' . number_format($distPct, 1, ',', '.') . '% longe',
            ];
        }
    }
@endphp
<div
    style="{{ Filament\Support\get_color_css_variables(Trend::getColor($product->trend), shades: [300, 500, 400, 600, 800]) }}"
    {{ $attributes->merge(['class' => 'pb-expandable-stat display-block w-full rounded-xl bg-gray-100 dark:bg-gray-800/30 shadow-sm ring-1 ring-gray-950/5 dark:ring-white/10']) }}
    x-data="{ expanded: false }"
>
    <div class="flex">
        <div
            class="flex-1 bg-white shadow-sm ring-1 ring-gray-950/5 dark:bg-gray-900 dark:ring-white/10 rounded-r-xl"
            :class="expanded ? 'rounded-bl-none' : ''"
        >
            <a class="flex gap-2" href="{{ $product->view_url }}">
                <div @class(['w-20 h-20 min-w-20 m-2 rounded-md overflow-hidden p-1 flex items-center', 'cursor-grab' => $draggable]) @if ($draggable) x-sortable-handle @endif>
                    <x-product-image :product="$product" />
                </div>
                <div class="my-1 flex flex-col min-w-0 justify-center pr-2" style="width: calc(100% - 5rem)">
                    <h3
                        class="mb-1 mt-2 text-sm text-gray-500 dark:text-gray-400 font-bold truncate min-w-0"
                        style="max-width: 15rem"
                        title="{{ $product->title }}"
                    >
                        {{ $product->title }}
                    </h3>
                    <div class="flex items-center flex-wrap gap-2">
                        @if ($latestPrice)
                            @if ($latestPrice->hasVisiblePrice())
                                <span class="text-2xl font-semibold leading-none">
                                    {{ $latestPrice->getUnitPriceFormatted() }}
                                </span>
                            @else
                                <span class="text-lg font-semibold text-gray-500 dark:text-gray-400">
                                    {{ __('Unavailable') }}
                                </span>
                            @endif
                        @else
                            <span class="text-lg font-semibold text-gray-500 dark:text-gray-400">
                                {{ __('Unavailable') }}
                            </span>
                        @endif

                        @if ($semaforo)
                            <span
                                style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:700;line-height:1.4;background:{{ $semaforo['bg'] }};border:1px solid {{ $semaforo['border'] }};color:{{ $semaforo['color'] }};white-space:nowrap;"
                                title="Meta de compra: {{ $metaFmt }}"
                            >
                                <span style="width:7px;height:7px;border-radius:9999px;background:{{ $semaforo['dot'] }};display:inline-block;"></span>
                                {{ $semaforo['label'] }}
                            </span>
                        @endif
                    </div>
                    @if ($latestPrice)
                        <span class="text-xs text-gray-500 dark:text-gray-400 font-bold display-block mt-1">
                            @if ($latestPrice->hasPriceFactor())
                                {{ __('per :unit', ['unit' => $latestPrice->getUnitOfMeasure()]) }}
                            @endif
                            {{ '@'.$latestPrice->getStoreName() }}
                            @if ($metaFmt)
                                <span style="font-weight:500;opacity:0.8;"> · Meta: {{ $metaFmt }}</span>
                            @endif
                        </span>
                    @endif
                    @if ($latestPrice?->hasPriceFactor())
                        <span class="text-xs text-gray-500 dark:text-gray-400 block mb-2 mt-1">
                            <x-price-factor-price :cache="$latestPrice" />
                        </span>
                    @endif
                    <div class="pb-card-badges block mb-2">
                        @include('components.product-badges', ['product' => $product])
                    </div>
                </div>
            </a>

            <div class="bg-custom-400/10 hover:bg-custom-400/20 rounded-br overflow-hidden">
                <x-range-chart :product="$product" height="40px"/>
            </div>

        </div>
        <div class="pb-expandable-stat__context">
            <button
                class="pb-expandable-stat__context-button h-full opacity-50 hover:opacity-100 py-3 bg-gray-100 dark:bg-gray-950 rounded-xl border dark:border-gray-500 border-neutral-500/50 dark:hover:border-neutral-700 border-t-0 border-r-0 rounded-tl-none rounded-br-none"
                :class="expanded ? 'items-end' : 'collapsed items-start'"
                @click="expanded = !expanded"
            >
                <x-filament::icon icon="heroicon-s-chevron-up" class="h-5 w-5"/>
            </button>
        </div>
    </div>

    @livewire('product-card-detail', ['product' => $product, 'showNextCheck' => false], key('product-card-detail-'.$product->id))
</div>
