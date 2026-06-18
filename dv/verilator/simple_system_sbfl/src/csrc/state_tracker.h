/***************************************************************************************
 * Copyright (c) 2020-2023 Institute of Computing Technology, Chinese Academy of
 * Sciences
 *
 * DiffTest is licensed under Mulan PSL v2.
 * You can use this software according to the terms and conditions of the Mulan
 * PSL v2. You may obtain a copy of Mulan PSL v2 at:
 *          http://license.coscl.org.cn/MulanPSL2
 *
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY
 * KIND, EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
 * NON-INFRINGEMENT, MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 *
 * See the Mulan PSL v2 for more details.
 ***************************************************************************************/

#ifndef STATE_TRACKER_H_
#define STATE_TRACKER_H_

#include <cstddef>
#include <cstdint>
#include <vector>

typedef struct {
  uint64_t value[32];
} ArchIntRegState;

typedef struct {
  uint64_t privilegeMode;
  uint64_t mstatus;
  uint64_t sstatus;
  uint64_t mepc;
  uint64_t sepc;
  uint64_t mtval;
  uint64_t stval;
  uint64_t mtvec;
  uint64_t stvec;
  uint64_t mcause;
  uint64_t scause;
  uint64_t satp;
  uint64_t mip;
  uint64_t mie;
  uint64_t mscratch;
  uint64_t sscratch;
  uint64_t mideleg;
  uint64_t medeleg;
} CSRState;

typedef struct {
  uint64_t pc;
  ArchIntRegState xrf;
  CSRState csr;
} State;

class StateTracker {
 public:
  StateTracker() = default;
  virtual ~StateTracker() = default;

  virtual const char *get_name() const = 0;
  virtual void reset() = 0;
  virtual void update(const State &state) = 0;

  // tracker figures
  virtual uint64_t get_total_states() = 0;
  virtual void *get_state_data(uint64_t i) = 0;

  // fuzzer feedback
  bool is_feedback = false;
  virtual void update_is_feedback(const char *state_name) {
    is_feedback = !state_name_cmp(state_name, get_name());
  }
  virtual void to_state_bytes(void *bytes) = 0;

 protected:
  static int state_name_cmp(const char *s1, const char *s2) {
    for (int i = 0; s1[i] || s2[i]; i++) {
      char a = (s1[i] >= 'A' && s1[i] <= 'Z') ? s1[i] + ('a' - 'A') : s1[i];
      char b = (s2[i] >= 'A' && s2[i] <= 'Z') ? s2[i] + ('a' - 'A') : s2[i];
      if (a != b) {
        return i + 1;
      }
    }
    return 0;
  }
};

class PCStateTracker : public StateTracker {
 public:
  PCStateTracker();
  ~PCStateTracker() = default;

  const char *get_name() const { return "PCState"; }
  void reset() override;
  void update(const State &state) override;

  uint64_t get_total_states() override;
  void *get_state_data(uint64_t i) override;

  void to_state_bytes(void *bytes) override;

 private:
  std::vector<uint64_t> tracker;
};

class ArchIntRegStateTracker : public StateTracker {
 public:
  ArchIntRegStateTracker();
  ~ArchIntRegStateTracker() = default;

  const char *get_name() const { return "ArchIntRegState"; }
  void reset() override;
  void update(const State &state) override;

  uint64_t get_total_states() override;
  void *get_state_data(uint64_t i) override;

  void to_state_bytes(void *bytes) override;

 private:
  std::vector<ArchIntRegState> tracker;
};

class CSRStateTracker : public StateTracker {
 public:
  CSRStateTracker();
  ~CSRStateTracker() = default;

  const char *get_name() const { return "CSRState"; }
  void reset() override;
  void update(const State &state) override;

  uint64_t get_total_states() override;
  void *get_state_data(uint64_t i) override;

  void to_state_bytes(void *bytes) override;

 private:
  std::vector<CSRState> tracker;
};

#endif  // __STATE_H
