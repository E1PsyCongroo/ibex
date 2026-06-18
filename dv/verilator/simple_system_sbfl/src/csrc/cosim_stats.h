#ifndef COSIM_STATS_H_
#define COSIM_STATS_H_

#include <vector>
#include <iostream>

#include "state_tracker.h"
#include "coverage.h"


class CosimStats {
public:
  // coverage statistics
  std::vector<Coverage *> cover;
  // committed state sequences
  std::vector<StateTracker *> state;

  CosimStats() {
#if VM_COVERAGE == 1
    auto c_verilator = new VerilatorCoverage;
    cover.push_back(c_verilator);
#endif // VM_COVERAGE
    auto s_pc = new PCStateTracker;
    auto s_int = new ArchIntRegStateTracker;
    auto s_csr = new CSRStateTracker;
    state.push_back(s_pc);
    state.push_back(s_int);
    state.push_back(s_csr);
    reset();
  };

  void reset() {
    for (auto cov: cover) {
      cov->reset();
    }
    for (auto seq: state) {
      seq->reset();
    }
  }

  void display() {
    for (auto cov: cover) {
      cov->display();
    }
  }

  void display_uncovered_points() {
    for (auto cov: cover) {
      cov->display_uncovered_points();
    }
  }

  void set_feedback_cover(const char *name) {
    for (auto cov: cover) {
      cov->update_is_feedback(name);
    }
  }

  Coverage *get_feedback_cover() {
    for (auto cov: cover) {
      if (cov->is_feedback) {
        return cov;
      }
    }
    std::cerr << "Failed to find any feedback coverage." << std::endl;
    return nullptr;
  }

  // StateTracker
  void update_state(const State &state) {
    for (auto s: this->state) {
      s->update(state);
    }
  }

  void set_feedback_state(const char *name) {
    for (auto s: state) {
      s->update_is_feedback(name);
    }
  }

  StateTracker *get_feedback_state() {
    for (auto s: state) {
      if (s->is_feedback) {
        return s;
      }
    }
    std::cerr << "Failed to find any feedback state sequence." << std::endl;
    return nullptr;
  }

};

extern CosimStats stats;

#endif
