#ifndef COVERAGE_H_
#define COVERAGE_H_

#include <cstdint>
#include <sstream>
#include <string>
#include <vector>

#if VM_COVERAGE == 1
#include "sim_ctrl_extension.h"
#endif

class Coverage : public SimCtrlExtension {
 public:
  Coverage() {};
  virtual void reset() = 0;
  virtual const char *get_name() = 0;
  virtual const char *get_cover_name(uint32_t i) { return "unknown"; }

  // coverage figures
  virtual uint32_t get_total_points() = 0;
  virtual uint32_t get_covered_points() = 0;
  inline double get_value() {
    return 100.0 * get_covered_points() / get_total_points();
  }

  // fuzzer feedback
  bool is_feedback = false;
  virtual void update_is_feedback(const char *cover_name) {
    is_feedback = !cover_name_cmp(cover_name, get_name());
  }
  virtual size_t cover_data_size() = 0;
  virtual void to_cover_data(void *data) = 0;

  virtual void display() = 0;
  virtual void display_uncovered_points() = 0;

 protected:
  static int cover_name_cmp(const char *s1, const char *s2) {
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

#if VM_COVERAGE == 1

struct VerilatorCoverPoint {
  std::string filename;
  std::string lineno;
  std::string column;
  std::string type;
  std::string linescov;
  std::string comment;
  std::string hier;
  std::string name;
  uint64_t count;

  void gen_name() {
    std::ostringstream ss;
    ss << "filename: " << filename << ", lineno: " << lineno
       << ", column: " << column << ", type: " << type
       << ", linescov: " << linescov << ", comment: " << comment
       << ", hier: " << hier;
    name = ss.str();
  }

} ;

class VerilatorCoverGroup {
 public:
  std::string name;
  std::vector<VerilatorCoverPoint> points;

  explicit VerilatorCoverGroup(std::string name)
      : name(std::move(name)), points{} {}
};

class VerilatorCoverage : public Coverage {
 public:
  VerilatorCoverage();
  void reset() override;

  const char *get_name() override { return "verilator"; }
  const char *get_cover_name(uint32_t i) override;

  // coverage figures
  uint32_t get_total_points() override;
  uint32_t get_covered_points() override;

  // fuzzer feedback
  void update_is_feedback(const char *cover_name) override;
  size_t cover_data_size() override;
  void to_cover_data(void *data) override;

  void display() override;
  void display_uncovered_points() override;

  bool ParseCLIArguments(int argc, char **argv, bool &exit_app) override {
    return true;
  };
  void PreExec() override;
  void PostExec() override;

 private:
  std::vector<VerilatorCoverGroup> cover;
  int feedback_group = -1;

  void load_from_file(const char *filename);
  void update_group(const std::string &type,
                    std::vector<VerilatorCoverPoint> &points);
  VerilatorCoverGroup *get();
  VerilatorCoverGroup *get(const std::string &type);
  static uint32_t cover_sum(const VerilatorCoverGroup &group);
  uint32_t cover_sum();
  void display(const VerilatorCoverGroup &group);
};

#endif

#endif
