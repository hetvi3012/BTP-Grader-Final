#include <vector>
#include <unordered_map>

using namespace std;

class Solution {
public:
    int countCoveredBuildings(int n, vector<vector<int>> buildings) {
        unordered_map<int, int> row_count;
        unordered_map<int, int> col_count 
        
        for (int i = 0; i < buildings.size(); i++) {
            row_count[buildings[i][0]]++;
            col_count[buildings[i][1]]++;
        }
        
        int ans = 0;
        for (int i = 0; i < buildings.size(); i++) {
            
            if (row_count[buildings[i][0]] >= 3 && col_count[buildings[i][1]] >= 3) {
                ans++;
            }
        }
        
        return ans 
};