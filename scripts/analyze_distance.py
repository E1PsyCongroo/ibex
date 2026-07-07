#!/usr/bin/env python3

import argparse
import re

import numpy as np
import matplotlib.pyplot as plt

from scipy import stats
from scipy.stats import skew, kurtosis


PATTERN = re.compile(
    r"cover_distance\s+([0-9.eE+-]+),\s+state_distance\s+([0-9.eE+-]+)"
)


def parse_log(filename):
    cover = []
    state = []

    with open(filename, "r") as f:
        for line in f:
            m = PATTERN.search(line)
            if m:
                cover.append(float(m.group(1)))
                state.append(float(m.group(2)))

    return np.asarray(cover), np.asarray(state)


def print_statistics(name, data):
    print("=" * 60)
    print(name)
    print("=" * 60)

    print(f"Count     : {len(data)}")
    print(f"Min       : {np.min(data):.8f}")
    print(f"Max       : {np.max(data):.8f}")
    print(f"Mean      : {np.mean(data):.8f}")
    print(f"Std       : {np.std(data, ddof=1):.8f}")
    print(f"Median    : {np.median(data):.8f}")

    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)

    print(f"Q1        : {q1:.8f}")
    print(f"Q3        : {q3:.8f}")
    print(f"IQR       : {q3-q1:.8f}")

    print(f"Skewness  : {skew(data):.4f}")
    print(f"Kurtosis  : {kurtosis(data):.4f}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze distance distributions."
    )
    parser.add_argument("logfile")
    args = parser.parse_args()

    cover, state = parse_log(args.logfile)

    if len(cover) == 0:
        print("No distance found.")
        return

    print_statistics("Cover Distance", cover)
    print_statistics("State Distance", state)

    #
    # Cover Histogram
    #
    plt.figure(figsize=(6, 4))
    plt.hist(cover, bins=50)
    plt.title("Cover Distance Distribution")
    plt.xlabel("Cover Distance")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig("cover_hist.png", dpi=300)

    #
    # State Histogram
    #
    plt.figure(figsize=(6, 4))
    plt.hist(state, bins=50)
    plt.title("State Distance Distribution")
    plt.xlabel("State Distance")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig("state_hist.png", dpi=300)

    #
    # Cover CDF
    #
    plt.figure(figsize=(6, 4))

    x = np.sort(cover)
    y = np.arange(1, len(x) + 1) / len(x)

    plt.plot(x, y)

    plt.xlabel("Cover Distance")
    plt.ylabel("CDF")
    plt.title("Cover Distance CDF")
    plt.grid(True)

    plt.tight_layout()
    plt.savefig("cover_cdf.png", dpi=300)

    #
    # State CDF
    #
    plt.figure(figsize=(6, 4))

    x = np.sort(state)
    y = np.arange(1, len(x) + 1) / len(x)

    plt.plot(x, y)

    plt.xlabel("State Distance")
    plt.ylabel("CDF")
    plt.title("State Distance CDF")
    plt.grid(True)

    plt.tight_layout()
    plt.savefig("state_cdf.png", dpi=300)

    #
    # Cover QQ Plot
    #
    plt.figure(figsize=(6, 6))

    stats.probplot(cover, dist="norm", plot=plt)

    plt.title("QQ Plot of Cover Distance")

    plt.tight_layout()
    plt.savefig("cover_qqplot.png", dpi=300)

    #
    # State QQ Plot
    #
    plt.figure(figsize=(6, 6))

    stats.probplot(state, dist="norm", plot=plt)

    plt.title("QQ Plot of State Distance")

    plt.tight_layout()
    plt.savefig("state_qqplot.png", dpi=300)

    #
    # Scatter
    #
    plt.figure(figsize=(6, 6))
    plt.scatter(cover, state, s=8)
    plt.xlabel("Cover Distance")
    plt.ylabel("State Distance")
    plt.title("Cover vs State Distance")
    plt.tight_layout()
    plt.savefig("cover_state_scatter.png", dpi=300)

    #
    # Hexbin (2D density)
    #
    plt.figure(figsize=(6, 6))

    plt.hexbin(
        cover,
        state,
        gridsize=35,
        mincnt=1,
    )

    plt.colorbar(label="Count")

    plt.xlabel("Cover Distance")
    plt.ylabel("State Distance")
    plt.title("Cover vs State Density")

    plt.tight_layout()
    plt.savefig("cover_state_hexbin.png", dpi=300)

    #
    # Boxplot
    #
    plt.figure(figsize=(6, 4))

    plt.boxplot(
        [cover, state],
        tick_labels=["Cover", "State"],
    )

    plt.title("Distance Boxplot")

    plt.tight_layout()
    plt.savefig("distance_boxplot.png", dpi=300)

    #
    # Correlation heatmap
    #
    corr = np.corrcoef(cover, state)

    plt.figure(figsize=(4, 4))

    plt.imshow(corr)

    plt.xticks([0, 1], ["Cover", "State"])
    plt.yticks([0, 1], ["Cover", "State"])

    plt.colorbar(label="Correlation")

    for i in range(2):
        for j in range(2):
            plt.text(
                j,
                i,
                f"{corr[i,j]:.3f}",
                ha="center",
                va="center",
                fontsize=12,
            )

    plt.title("Correlation Matrix")

    plt.tight_layout()
    plt.savefig("correlation_heatmap.png", dpi=300)

    print("Pearson correlation:", np.corrcoef(cover, state)[0, 1])

    print()
    print("Generated figures:")
    print("  cover_hist.png")
    print("  state_hist.png")
    print("  cover_cdf.png")
    print("  state_cdf.png")
    print("  cover_qqplot.png")
    print("  state_qqplot.png")
    print("  cover_state_scatter.png")
    print("  cover_state_hexbin.png")
    print("  distance_boxplot.png")
    print("  correlation_heatmap.png")


if __name__ == "__main__":
    main()
